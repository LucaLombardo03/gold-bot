import datetime
import json
import sys
import time

import MetaTrader5 as mt5

from logger_setup import get_logger
from utils import (
    calculate_adx,
    calculate_atr,
    calculate_ema,
    calculate_lot_size,
    get_open_positions,
    get_prices,
    manage_trailing_stop,
    open_trade,
    seconds_to_next_candle,
    send_telegram,
)


def load_config(path: str = "config.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    cfg = load_config()
    log = get_logger("bot")

    connected = False
    try:
        connected = mt5.initialize(
            login=cfg["mt5_login"],
            password=cfg["mt5_password"],
            server=cfg["mt5_server"],
        )
    except Exception as exc:
        log.critical("Eccezione durante mt5.initialize: %s", exc)

    if not connected:
        log.critical("Connessione MT5 fallita. Errore: %s", mt5.last_error())
        sys.exit(1)

    try:
        account = mt5.account_info()
    except Exception as exc:
        log.critical("Eccezione in account_info: %s", exc)
        mt5.shutdown()
        sys.exit(1)

    if account is None:
        log.critical("account_info ha restituito None. Errore MT5: %s", mt5.last_error())
        mt5.shutdown()
        sys.exit(1)

    symbol = cfg["symbol"]
    timeframe = cfg["timeframe"]
    atr_mult_sl = float(cfg["atr_mult_sl"])
    atr_mult_tp = float(cfg["atr_mult_tp"])
    ema_slow = int(cfg["ema_slow"])
    ema_fast = int(cfg["ema_fast"])
    atr_period = int(cfg["atr_period"])
    drawdown_limit = float(cfg["drawdown_limit"])
    session_start = int(cfg["session_start_utc"])
    session_end = int(cfg["session_end_utc"])
    lunch_start = int(cfg.get("lunch_break_start_utc", -1))
    lunch_end = int(cfg.get("lunch_break_end_utc", -1))
    tg_token = cfg["telegram_token"]
    tg_chat = cfg["telegram_chat_id"]
    dry_run = bool(cfg.get("dry_run", False))

    risk_pct = float(cfg.get("risk_pct", 0.01))
    min_lot = float(cfg.get("min_lot", 0.01))
    max_lot = float(cfg.get("max_lot", 1.0))

    trail_enabled = bool(cfg.get("trail_enabled", False))
    trail_activation_r = float(cfg.get("trail_activation_r", 1.0))
    trail_offset_atr = float(cfg.get("trail_offset_atr", 0.75))

    adx_filter_enabled = bool(cfg.get("adx_filter_enabled", False))
    adx_period = int(cfg.get("adx_period", 14))
    adx_threshold = float(cfg.get("adx_threshold", 25.0))

    initial_balance = account.balance
    log.info("Bot avviato. Saldo iniziale: %.2f USD", initial_balance)
    if dry_run:
        log.warning("DRY RUN attivo - nessun ordine reale verrà inviato")

    last_candle_time = None

    try:
        while True:
            # --- Step 1: Check drawdown ---
            try:
                current_account = mt5.account_info()
            except Exception as exc:
                log.error("Eccezione in account_info durante drawdown check: %s", exc)
                current_account = None

            if current_account is not None:
                current_balance = current_account.balance
                if current_balance < initial_balance * drawdown_limit:
                    msg = (
                        f"HARD STOP: drawdown limite raggiunto | "
                        f"Saldo: {current_balance:.2f} USD | "
                        f"Limite: {initial_balance * drawdown_limit:.2f} USD"
                    )
                    log.critical(msg)
                    send_telegram(tg_token, tg_chat, msg)
                    mt5.shutdown()
                    sys.exit(0)
            else:
                log.warning("account_info None durante drawdown check, skip.")
                current_balance = initial_balance

            # --- Step 2: Check sessione ---
            utc_hour = datetime.datetime.now(datetime.timezone.utc).hour
            in_lunch = (
                lunch_start >= 0
                and lunch_end >= 0
                and lunch_start <= utc_hour < lunch_end
            )
            if not (session_start <= utc_hour < session_end) or in_lunch:
                reason = "pausa pranzo" if in_lunch else "fuori sessione"
                log.info("Ora UTC %d: %s, attendo...", utc_hour, reason)
                time.sleep(seconds_to_next_candle(timeframe))
                continue

            # --- Step 3: Estrai dati ---
            df = get_prices(symbol, timeframe, bars=300)
            if df is None:
                log.error("get_prices ha restituito None, riprovo tra 60s")
                time.sleep(60)
                continue

            if len(df) < ema_slow + 5:
                log.warning("Barre insufficienti (%d), riprovo tra 60s", len(df))
                time.sleep(60)
                continue

            # --- Step 4: Check nuova candela ---
            # Convert to plain Python datetime to guarantee consistent equality
            # semantics regardless of pandas/numpy version differences.
            candle_time = df["time"].iloc[-2].to_pydatetime()
            if last_candle_time == candle_time:
                log.debug("Stessa candela (%s), skip elaborazione", candle_time)
                time.sleep(seconds_to_next_candle(timeframe))
                continue
            last_candle_time = candle_time

            # --- Step 5: Calcola indicatori ---
            close = df["close"]
            ema200_series = calculate_ema(close, ema_slow)
            ema50_series = calculate_ema(close, ema_fast)
            atr = calculate_atr(df, atr_period)

            ema200 = ema200_series.iloc[-2]
            ema50 = ema50_series.iloc[-2]
            prev_close = close.iloc[-2]

            adx = calculate_adx(df, adx_period) if adx_filter_enabled else None

            sl_distance = atr * atr_mult_sl
            lot = calculate_lot_size(
                symbol, current_balance, risk_pct, sl_distance,
                min_lot=min_lot, max_lot=max_lot,
            )

            open_pos = get_open_positions(symbol)

            if adx_filter_enabled and adx is not None:
                log.info(
                    "Indicatori | Candela: %s | Prezzo: %.5f | EMA50: %.5f | "
                    "EMA200: %.5f | ATR: %.5f | ADX: %.1f | Lot: %.2f | "
                    "Posizioni: %d | Saldo: %.2f USD",
                    candle_time, prev_close, ema50, ema200, atr, adx,
                    lot, open_pos, current_balance,
                )
            else:
                log.info(
                    "Indicatori | Candela: %s | Prezzo: %.5f | EMA50: %.5f | "
                    "EMA200: %.5f | ATR: %.5f | Lot: %.2f | Posizioni: %d | Saldo: %.2f USD",
                    candle_time, prev_close, ema50, ema200, atr,
                    lot, open_pos, current_balance,
                )

            # --- Step 5.5: Trailing stop ---
            if trail_enabled:
                manage_trailing_stop(
                    symbol, atr, atr_mult_sl,
                    trail_activation_r, trail_offset_atr,
                    dry_run=dry_run,
                )

            # --- Step 6: Logica segnali ---
            trend_ok = (not adx_filter_enabled) or (adx is not None and adx >= adx_threshold)

            buy_cond = (
                prev_close > ema200
                and ema50 > prev_close
                and open_pos == 0
                and trend_ok
            )
            sell_cond = (
                prev_close < ema200
                and ema50 < prev_close
                and open_pos == 0
                and trend_ok
            )

            log.info(
                "Segnali valutati | BUY=%s (close>EMA200: %s, EMA50>close: %s, pos==0: %s, trend_ok: %s) | "
                "SELL=%s (close<EMA200: %s, EMA50<close: %s, pos==0: %s, trend_ok: %s)",
                buy_cond,
                prev_close > ema200,
                ema50 > prev_close,
                open_pos == 0,
                trend_ok,
                sell_cond,
                prev_close < ema200,
                ema50 < prev_close,
                open_pos == 0,
                trend_ok,
            )

            if buy_cond:
                try:
                    tick = mt5.symbol_info_tick(symbol)
                except Exception as exc:
                    log.error("Eccezione in symbol_info_tick (BUY): %s", exc)
                    tick = None

                if tick is not None:
                    entry = tick.ask
                    sl = entry - sl_distance
                    tp = entry + (atr * atr_mult_tp)
                    if dry_run:
                        log.info(
                            "DRY RUN - ordine non inviato | BUY | Entry: %.5f | "
                            "SL: %.5f | TP: %.5f | Lot: %.2f",
                            entry, sl, tp, lot,
                        )
                    else:
                        success = open_trade(symbol, mt5.ORDER_TYPE_BUY, lot, sl, tp)
                        if success:
                            msg = (
                                f"✅ BUY aperto | Entry: {entry:.5f} | "
                                f"SL: {sl:.5f} | TP: {tp:.5f} | Lot: {lot:.2f}"
                            )
                            send_telegram(tg_token, tg_chat, msg)
                else:
                    log.error("symbol_info_tick None per BUY, ordine saltato")

            elif sell_cond:
                try:
                    tick = mt5.symbol_info_tick(symbol)
                except Exception as exc:
                    log.error("Eccezione in symbol_info_tick (SELL): %s", exc)
                    tick = None

                if tick is not None:
                    entry = tick.bid
                    sl = entry + sl_distance
                    tp = entry - (atr * atr_mult_tp)
                    if dry_run:
                        log.info(
                            "DRY RUN - ordine non inviato | SELL | Entry: %.5f | "
                            "SL: %.5f | TP: %.5f | Lot: %.2f",
                            entry, sl, tp, lot,
                        )
                    else:
                        success = open_trade(symbol, mt5.ORDER_TYPE_SELL, lot, sl, tp)
                        if success:
                            msg = (
                                f"✅ SELL aperto | Entry: {entry:.5f} | "
                                f"SL: {sl:.5f} | TP: {tp:.5f} | Lot: {lot:.2f}"
                            )
                            send_telegram(tg_token, tg_chat, msg)
                else:
                    log.error("symbol_info_tick None per SELL, ordine saltato")

            # --- Step 7: Status line e sleep ---
            log.info(
                "Ciclo completato | Prezzo: %.5f | EMA50: %.5f | EMA200: %.5f | "
                "ATR: %.5f | Posizioni aperte: %d | Saldo: %.2f USD",
                prev_close, ema50, ema200, atr, open_pos, current_balance,
            )

            sleep_secs = seconds_to_next_candle(timeframe)
            log.info("Sleep %ds fino alla prossima candela", sleep_secs)
            time.sleep(sleep_secs)

    except KeyboardInterrupt:
        log.info("Bot fermato manualmente")
        mt5.shutdown()


if __name__ == "__main__":
    main()
