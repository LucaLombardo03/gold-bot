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
    check_closed_trades,
    get_daily_summary,
    get_open_positions,
    get_prices,
    manage_breakeven,
    manage_trailing_stop,
    open_trade,
    seconds_to_next_candle,
    send_telegram,
    try_reconnect_mt5,
)


def load_config(path: 
tr = "config.json") -> dict:
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

    symbol        = cfg["symbol"]
    timeframe     = cfg["timeframe"]
    atr_mult_sl   = float(cfg["atr_mult_sl"])
    atr_mult_tp   = float(cfg["atr_mult_tp"])
    ema_slow      = int(cfg["ema_slow"])
    ema_fast      = int(cfg["ema_fast"])
    atr_period    = int(cfg["atr_period"])
    drawdown_limit = float(cfg["drawdown_limit"])
    session_start = int(cfg["session_start_utc"])
    session_end   = int(cfg["session_end_utc"])
    lunch_start   = int(cfg.get("lunch_break_start_utc", -1))
    lunch_end     = int(cfg.get("lunch_break_end_utc", -1))
    tg_token      = cfg["telegram_token"]
    tg_chat       = cfg["telegram_chat_id"]
    dry_run       = bool(cfg.get("dry_run", False))

    risk_pct  = float(cfg.get("risk_pct", 0.01))
    min_lot   = float(cfg.get("min_lot", 0.01))
    max_lot   = float(cfg.get("max_lot", 1.0))

    trail_enabled      = bool(cfg.get("trail_enabled", False))
    trail_activation_r = float(cfg.get("trail_activation_r", 1.0))
    trail_offset_atr   = float(cfg.get("trail_offset_atr", 0.75))

    breakeven_enabled      = bool(cfg.get("breakeven_enabled", True))
    breakeven_activation_r = float(cfg.get("breakeven_activation_r", 1.0))

    adx_filter_enabled = bool(cfg.get("adx_filter_enabled", False))
    adx_period         = int(cfg.get("adx_period", 14))
    adx_threshold      = float(cfg.get("adx_threshold", 25.0))

    max_daily_trades = int(cfg.get("max_daily_trades", 3))

    initial_balance = account.balance
    bot_start_time  = datetime.datetime.now(datetime.timezone.utc)

    log.info("Bot avviato. Saldo iniziale: %.2f USD", initial_balance)
    if dry_run:
        log.warning("DRY RUN attivo - nessun ordine reale verrà inviato")

    mode_label  = "🟡 DRY RUN" if dry_run else "🟢 LIVE"
    adx_label   = f"ADX >= {adx_threshold}" if adx_filter_enabled else "off"
    trail_label = f"attivo (>{trail_activation_r}R)" if trail_enabled else "off"
    be_label    = f"attivo (>{breakeven_activation_r}R)" if breakeven_enabled else "off"
    send_telegram(
        tg_token, tg_chat,
        f"🤖 Gold Bot avviato {mode_label}\n"
        f"Simbolo: {symbol} | TF: {timeframe}\n"
        f"Saldo: {initial_balance:.2f} USD\n"
        f"Rischio/trade: {risk_pct*100:.1f}% | Lot: {min_lot}-{max_lot}\n"
        f"Sessione UTC: {session_start}:00-{session_end}:00\n"
        f"ADX filter: {adx_label}\n"
        f"Trailing: {trail_label} | Breakeven: {be_label}\n"
        f"Max trade/giorno: {max_daily_trades}",
    )

    last_candle_time   = None
    notified_tickets   = set()
    daily_trades       = 0
    last_trade_date    = None
    last_report_date   = None

    try:
        while True:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            today   = now_utc.date()

            # Reset contatore giornaliero a mezzanotte UTC
            if last_trade_date != today:
                daily_trades    = 0
                last_trade_date = today

            # --- Step 1: Check connessione e drawdown ---
            try:
                current_account = mt5.account_info()
            except Exception as exc:
                log.error("Eccezione in account_info durante drawdown check: %s", exc)
                current_account = None

            if current_account is None:
                log.warning("MT5 disconnesso — tentativo reconnect...")
                send_telegram(tg_token, tg_chat, "⚠️ MT5 disconnesso — tentativo reconnect...")
                reconnected = try_reconnect_mt5(
                    cfg["mt5_login"], cfg["mt5_password"], cfg["mt5_server"],
                    tg_token=tg_token, tg_chat=tg_chat,
                )
                if not reconnected:
                    mt5.shutdown()
                    sys.exit(1)
                current_balance = initial_balance
            else:
                current_balance = current_account.balance
                if current_balance < initial_balance * drawdown_limit:
                    msg = (
                        f"🛑 HARD STOP: drawdown limite raggiunto\n"
                        f"Saldo: {current_balance:.2f} USD\n"
                        f"Limite: {initial_balance * drawdown_limit:.2f} USD"
                    )
                    log.critical(msg)
                    send_telegram(tg_token, tg_chat, msg)
                    mt5.shutdown()
                    sys.exit(0)

            # --- Step 1.5: Controlla trade chiusi ---
            check_closed_trades(
                symbol, bot_start_time, notified_tickets,
                tg_token=tg_token, tg_chat=tg_chat,
            )

            # --- Step 1.6: Report giornaliero a fine sessione ---
            if last_report_date != today and now_utc.hour >= session_end:
                day_start = datetime.datetime.combine(today, datetime.time.min).replace(
                    tzinfo=datetime.timezone.utc
                )
                trades_count, day_pnl = get_daily_summary(symbol, day_start)
                pnl_emoji = "📈" if day_pnl >= 0 else "📉"
                send_telegram(
                    tg_token, tg_chat,
                    f"{pnl_emoji} Report giornaliero — {today}\n"
                    f"Trade chiusi: {trades_count}\n"
                    f"P&L giorno: {day_pnl:+.2f} USD\n"
                    f"Saldo: {current_balance:.2f} USD",
                )
                log.info(
                    "Report giornaliero | Trade: %d | P&L: %.2f USD | Saldo: %.2f USD",
                    trades_count, day_pnl, current_balance,
                )
                last_report_date = today

            # --- Step 2: Check sessione ---
            utc_hour = now_utc.hour
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
            close         = df["close"]
            ema200_series = calculate_ema(close, ema_slow)
            ema50_series  = calculate_ema(close, ema_fast)
            atr           = calculate_atr(df, atr_period)

            ema200     = ema200_series.iloc[-2]
            ema50      = ema50_series.iloc[-2]
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
                    "Posizioni: %d | Trade oggi: %d/%d | Saldo: %.2f USD",
                    candle_time, prev_close, ema50, ema200, atr, adx,
                    lot, open_pos, daily_trades, max_daily_trades, current_balance,
                )
            else:
                log.info(
                    "Indicatori | Candela: %s | Prezzo: %.5f | EMA50: %.5f | "
                    "EMA200: %.5f | ATR: %.5f | Lot: %.2f | "
                    "Posizioni: %d | Trade oggi: %d/%d | Saldo: %.2f USD",
                    candle_time, prev_close, ema50, ema200, atr,
                    lot, open_pos, daily_trades, max_daily_trades, current_balance,
                )

            # --- Step 5.5: Breakeven e trailing stop ---
            if breakeven_enabled:
                manage_breakeven(
                    symbol, atr, atr_mult_sl,
                    breakeven_activation_r,
                    dry_run=dry_run,
                    tg_token=tg_token, tg_chat=tg_chat,
                )

            if trail_enabled:
                manage_trailing_stop(
                    symbol, atr, atr_mult_sl,
                    trail_activation_r, trail_offset_atr,
                    dry_run=dry_run,
                    tg_token=tg_token, tg_chat=tg_chat,
                )

            # --- Step 6: Logica segnali ---
            trend_ok       = (not adx_filter_enabled) or (adx is not None and adx >= adx_threshold)
            daily_limit_ok = daily_trades < max_daily_trades

            buy_cond = (
                prev_close > ema200
                and ema50 > prev_close
                and open_pos == 0
                and trend_ok
                and daily_limit_ok
            )
            sell_cond = (
                prev_close < ema200
                and ema50 < prev_close
                and open_pos == 0
                and trend_ok
                and daily_limit_ok
            )

            log.info(
                "Segnali | BUY=%s | SELL=%s | trend_ok=%s | daily=%d/%d",
                buy_cond, sell_cond, trend_ok, daily_trades, max_daily_trades,
            )

            if not daily_limit_ok:
                log.info("Max trade giornalieri raggiunto (%d/%d), skip segnali", daily_trades, max_daily_trades)

            if buy_cond:
                try:
                    tick = mt5.symbol_info_tick(symbol)
                except Exception as exc:
                    log.error("Eccezione in symbol_info_tick (BUY): %s", exc)
                    tick = None

                if tick is not None:
                    entry   = tick.ask
                    sl      = entry - sl_distance
                    tp      = entry + (atr * atr_mult_tp)
                    adx_str = f" | ADX: {adx:.1f}" if adx is not None else ""
                    if dry_run:
                        log.info(
                            "DRY RUN - ordine non inviato | BUY | Entry: %.5f | "
                            "SL: %.5f | TP: %.5f | Lot: %.2f",
                            entry, sl, tp, lot,
                        )
                        send_telegram(
                            tg_token, tg_chat,
                            f"🟡 [DRY RUN] Segnale BUY\n"
                            f"Entry: {entry:.5f}\n"
                            f"SL: {sl:.5f} (-{sl_distance:.5f})\n"
                            f"TP: {tp:.5f} (+{atr * atr_mult_tp:.5f})\n"
                            f"Lot: {lot:.2f} | Rischio: {risk_pct*100:.1f}%{adx_str}",
                        )
                    else:
                        success = open_trade(symbol, mt5.ORDER_TYPE_BUY, lot, sl, tp)
                        if success:
                            daily_trades += 1
                            send_telegram(
                                tg_token, tg_chat,
                                f"✅ BUY aperto\n"
                                f"Entry: {entry:.5f}\n"
                                f"SL: {sl:.5f} (-{sl_distance:.5f})\n"
                                f"TP: {tp:.5f} (+{atr * atr_mult_tp:.5f})\n"
                                f"Lot: {lot:.2f} | Rischio: {risk_pct*100:.1f}%{adx_str}\n"
                                f"Trade oggi: {daily_trades}/{max_daily_trades}",
                            )
                else:
                    log.error("symbol_info_tick None per BUY, ordine saltato")

            elif sell_cond:
                try:
                    tick = mt5.symbol_info_tick(symbol)
                except Exception as exc:
                    log.error("Eccezione in symbol_info_tick (SELL): %s", exc)
                    tick = None

                if tick is not None:
                    entry   = tick.bid
                    sl      = entry + sl_distance
                    tp      = entry - (atr * atr_mult_tp)
                    adx_str = f" | ADX: {adx:.1f}" if adx is not None else ""
                    if dry_run:
                        log.info(
                            "DRY RUN - ordine non inviato | SELL | Entry: %.5f | "
                            "SL: %.5f | TP: %.5f | Lot: %.2f",
                            entry, sl, tp, lot,
                        )
                        send_telegram(
                            tg_token, tg_chat,
                            f"🟡 [DRY RUN] Segnale SELL\n"
                            f"Entry: {entry:.5f}\n"
                            f"SL: {sl:.5f} (+{sl_distance:.5f})\n"
                            f"TP: {tp:.5f} (-{atr * atr_mult_tp:.5f})\n"
                            f"Lot: {lot:.2f} | Rischio: {risk_pct*100:.1f}%{adx_str}",
                        )
                    else:
                        success = open_trade(symbol, mt5.ORDER_TYPE_SELL, lot, sl, tp)
                        if success:
                            daily_trades += 1
                            send_telegram(
                                tg_token, tg_chat,
                                f"✅ SELL aperto\n"
                                f"Entry: {entry:.5f}\n"
                                f"SL: {sl:.5f} (+{sl_distance:.5f})\n"
                                f"TP: {tp:.5f} (-{atr * atr_mult_tp:.5f})\n"
                                f"Lot: {lot:.2f} | Rischio: {risk_pct*100:.1f}%{adx_str}\n"
                                f"Trade oggi: {daily_trades}/{max_daily_trades}",
                            )
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
        send_telegram(tg_token, tg_chat, "🔴 Gold Bot fermato manualmente")
        mt5.shutdown()


if __name__ == "__main__":
    main()
