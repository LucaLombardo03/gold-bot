import datetime

import MetaTrader5 as mt5
import numpy as np
import pandas as pd
import requests

from logger_setup import get_logger

log = get_logger("utils")

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}

_CANDLE_SECONDS: dict[str, int] = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "W1": 604800,
    "MN1": 2592000,
}

MAGIC_NUMBER = 20240101

_ORDER_DIR: dict[int, str] = {
    mt5.ORDER_TYPE_BUY: "BUY",
    mt5.ORDER_TYPE_SELL: "SELL",
}


def get_prices(symbol: str, timeframe: str, bars: int = 300) -> pd.DataFrame | None:
    tf = TIMEFRAME_MAP.get(timeframe)
    if tf is None:
        log.error("Timeframe non valido: %s", timeframe)
        return None
    try:
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    except Exception as exc:
        log.error("Eccezione in copy_rates_from_pos: %s", exc)
        return None

    if rates is None or len(rates) == 0:
        log.error(
            "copy_rates_from_pos ha restituito dati vuoti per %s. Errore MT5: %s",
            symbol,
            mt5.last_error(),
        )
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df[["time", "open", "high", "low", "close", "tick_volume"]]


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calculate_atr(df: pd.DataFrame, period: int) -> float:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    hl = (high - low).to_numpy()
    hpc = (high - prev_close).abs().to_numpy()
    lpc = (low - prev_close).abs().to_numpy()
    tr = pd.Series(np.maximum(hl, np.maximum(hpc, lpc)), index=df.index)

    # Wilder's smoothing uses alpha = 1/period (com = period - 1)
    atr_series = tr.ewm(com=period - 1, adjust=False).mean()

    # -1 is the still-open candle; -2 is the last closed bar
    value = float(atr_series.iloc[-2])
    if pd.isna(value):
        log.error("ATR è NaN — serie troppo corta per il periodo %d", period)
        return float("nan")
    return value


def seconds_to_next_candle(timeframe: str = "H1") -> int:
    interval = _CANDLE_SECONDS.get(timeframe, _CANDLE_SECONDS["H1"])
    now = datetime.datetime.now(datetime.timezone.utc)
    total_seconds = (
        now.hour * 3600 + now.minute * 60 + now.second + now.microsecond / 1_000_000
    )
    elapsed = total_seconds % interval
    remaining = interval - elapsed
    return max(5, int(remaining) + 5)


def get_open_positions(symbol: str) -> int:
    try:
        positions = mt5.positions_get(symbol=symbol)
    except Exception as exc:
        log.error("Eccezione in positions_get: %s", exc)
        return 0

    if positions is None:
        return 0
    return len(positions)


def send_telegram(token: str, chat_id: str, message: str) -> None:
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "text": message},
            timeout=10,
        )
        if not resp.ok:
            log.error("Telegram risposta non-OK: %s %s", resp.status_code, resp.text)
    except requests.RequestException as exc:
        log.error("Errore rete Telegram: %s", exc)


def calculate_lot_size(
    symbol: str,
    balance: float,
    risk_pct: float,
    sl_distance: float,
    min_lot: float = 0.01,
    max_lot: float = 1.0,
) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        log.error("symbol_info None in calculate_lot_size, uso min_lot")
        return min_lot
    contract_size = info.trade_contract_size  # 100 per XAUUSD
    volume_step = info.volume_step            # tipicamente 0.01

    if sl_distance <= 0:
        return min_lot

    risk_amount = balance * risk_pct
    lot = risk_amount / (sl_distance * contract_size)
    lot = max(min_lot, min(max_lot, lot))
    lot = round(round(lot / volume_step) * volume_step, 2)
    return lot


def calculate_adx(df: pd.DataFrame, period: int = 14) -> float:
    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    prev_close = close.shift(1)
    tr = pd.Series(
        np.maximum(
            (high - low).to_numpy(),
            np.maximum(
                (high - prev_close).abs().to_numpy(),
                (low - prev_close).abs().to_numpy(),
            ),
        ),
        index=df.index,
    )

    atr_s = tr.ewm(com=period - 1, adjust=False).mean()
    plus_di = (
        100
        * pd.Series(plus_dm, index=df.index).ewm(com=period - 1, adjust=False).mean()
        / atr_s
    )
    minus_di = (
        100
        * pd.Series(minus_dm, index=df.index).ewm(com=period - 1, adjust=False).mean()
        / atr_s
    )
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di)).fillna(0)
    adx_series = dx.ewm(com=period - 1, adjust=False).mean()

    value = float(adx_series.iloc[-2])
    if pd.isna(value):
        log.error("ADX è NaN — serie troppo corta per il periodo %d", period)
        return 0.0
    return value


def try_reconnect_mt5(
    login: int,
    password: str,
    server: str,
    tg_token: str = "",
    tg_chat: str = "",
    max_attempts: int = 3,
) -> bool:
    for attempt in range(1, max_attempts + 1):
        log.warning("Tentativo reconnect MT5 %d/%d...", attempt, max_attempts)
        try:
            mt5.shutdown()
            import time as _time
            _time.sleep(5)
            connected = mt5.initialize(login=login, password=password, server=server)
            if connected:
                log.info("Reconnect MT5 riuscito al tentativo %d", attempt)
                send_telegram(
                    tg_token, tg_chat,
                    f"🟢 MT5 riconnesso (tentativo {attempt}/{max_attempts})",
                )
                return True
        except Exception as exc:
            log.error("Eccezione durante reconnect tentativo %d: %s", attempt, exc)
        import time as _time
        _time.sleep(30)

    log.critical("Reconnect MT5 fallito dopo %d tentativi", max_attempts)
    send_telegram(
        tg_token, tg_chat,
        f"🔴 MT5 disconnesso — reconnect fallito dopo {max_attempts} tentativi. Bot fermato.",
    )
    return False


def check_closed_trades(
    symbol: str,
    since: datetime.datetime,
    notified_tickets: set,
    tg_token: str = "",
    tg_chat: str = "",
) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        deals = mt5.history_deals_get(since, now, group=symbol)
    except Exception as exc:
        log.error("Eccezione in history_deals_get: %s", exc)
        return

    if deals is None:
        return

    for deal in deals:
        if deal.ticket in notified_tickets:
            continue
        if deal.magic != MAGIC_NUMBER:
            continue
        if deal.entry != mt5.DEAL_ENTRY_OUT:
            continue

        notified_tickets.add(deal.ticket)

        direction = "BUY" if deal.type == mt5.DEAL_TYPE_SELL else "SELL"  # exit deal type is opposite
        net = deal.profit + deal.commission + deal.swap
        result_emoji = "✅" if deal.profit >= 0 else "❌"

        log.info(
            "Trade chiuso | Ticket %d | %s | P&L: %.2f USD | Netto: %.2f USD",
            deal.ticket, direction, deal.profit, net,
        )
        send_telegram(
            tg_token, tg_chat,
            f"{result_emoji} Trade chiuso\n"
            f"{symbol} | {direction}\n"
            f"P&L: {deal.profit:+.2f} USD\n"
            f"Commissione: {deal.commission:.2f} | Swap: {deal.swap:.2f}\n"
            f"Netto: {net:+.2f} USD",
        )


def get_daily_summary(symbol: str, day_start: datetime.datetime) -> tuple[int, float]:
    """Restituisce (numero_trade_chiusi, pnl_totale) per la giornata corrente."""
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        deals = mt5.history_deals_get(day_start, now, group=symbol)
    except Exception as exc:
        log.error("Eccezione in history_deals_get (daily summary): %s", exc)
        return 0, 0.0

    if deals is None:
        return 0, 0.0

    count = 0
    total_pnl = 0.0
    for deal in deals:
        if deal.magic != MAGIC_NUMBER:
            continue
        if deal.entry != mt5.DEAL_ENTRY_OUT:
            continue
        count += 1
        total_pnl += deal.profit + deal.commission + deal.swap

    return count, total_pnl


def manage_breakeven(
    symbol: str,
    atr: float,
    atr_mult_sl: float,
    breakeven_activation_r: float = 1.0,
    dry_run: bool = False,
    tg_token: str = "",
    tg_chat: str = "",
) -> None:
    try:
        positions = mt5.positions_get(symbol=symbol)
    except Exception as exc:
        log.error("Eccezione in positions_get (breakeven): %s", exc)
        return

    if not positions:
        return

    info = mt5.symbol_info(symbol)
    if info is None:
        log.error("symbol_info None in manage_breakeven")
        return
    digits = info.digits

    activation_distance = atr * atr_mult_sl * breakeven_activation_r

    for pos in positions:
        direction = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"
        new_sl = round(pos.price_open, digits)

        if pos.type == mt5.ORDER_TYPE_BUY:
            if pos.sl >= pos.price_open > 0:
                continue  # già a breakeven o meglio
            profit_distance = pos.price_current - pos.price_open
        else:
            if 0 < pos.sl <= pos.price_open:
                continue  # già a breakeven o meglio
            profit_distance = pos.price_open - pos.price_current

        if profit_distance < activation_distance:
            continue

        if dry_run:
            log.info(
                "DRY RUN - breakeven non inviato | Ticket %d | SL → %.5f (entry)",
                pos.ticket, new_sl,
            )
            send_telegram(
                tg_token, tg_chat,
                f"🔒 [DRY RUN] Breakeven\n"
                f"Ticket: {pos.ticket} | {direction}\n"
                f"SL → {new_sl:.5f} (entry)",
            )
            continue

        try:
            result = mt5.order_send({
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": symbol,
                "sl": new_sl,
                "tp": pos.tp,
                "position": pos.ticket,
            })
        except Exception as exc:
            log.error("Eccezione in order_send (breakeven) Ticket %d: %s", pos.ticket, exc)
            continue

        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info("Breakeven impostato | Ticket %d | SL → %.5f", pos.ticket, new_sl)
            send_telegram(
                tg_token, tg_chat,
                f"🔒 Breakeven impostato\n"
                f"Ticket: {pos.ticket} | {direction}\n"
                f"SL → {new_sl:.5f} (entry)",
            )
        else:
            log.warning(
                "Breakeven fallito | Ticket %d | retcode: %s",
                pos.ticket,
                result.retcode if result else "None",
            )


def manage_trailing_stop(
    symbol: str,
    atr: float,
    atr_mult_sl: float,
    trail_activation_r: float,
    trail_offset_atr: float,
    dry_run: bool = False,
    tg_token: str = "",
    tg_chat: str = "",
) -> None:
    try:
        positions = mt5.positions_get(symbol=symbol)
    except Exception as exc:
        log.error("Eccezione in positions_get (trailing): %s", exc)
        return

    if not positions:
        return

    info = mt5.symbol_info(symbol)
    if info is None:
        log.error("symbol_info None in manage_trailing_stop")
        return
    digits = info.digits

    activation_distance = atr * atr_mult_sl * trail_activation_r
    trail_offset = trail_offset_atr * atr

    for pos in positions:
        profit_distance = abs(pos.price_current - pos.price_open)
        if profit_distance < activation_distance:
            continue

        if pos.type == mt5.ORDER_TYPE_BUY:
            new_sl = round(pos.price_current - trail_offset, digits)
            if pos.sl > 0 and new_sl <= pos.sl:
                continue
        else:
            new_sl = round(pos.price_current + trail_offset, digits)
            if pos.sl > 0 and new_sl >= pos.sl:
                continue

        direction = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"

        if dry_run:
            log.info(
                "DRY RUN - trailing stop non inviato | Ticket %d | Nuovo SL: %.5f",
                pos.ticket,
                new_sl,
            )
            send_telegram(
                tg_token, tg_chat,
                f"🔁 [DRY RUN] Trailing stop\n"
                f"Ticket: {pos.ticket} | {direction}\n"
                f"Prezzo corrente: {pos.price_current:.5f}\n"
                f"Nuovo SL: {new_sl:.5f}",
            )
            continue

        try:
            result = mt5.order_send({
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": symbol,
                "sl": new_sl,
                "tp": pos.tp,
                "position": pos.ticket,
            })
        except Exception as exc:
            log.error("Eccezione in order_send (trailing) Ticket %d: %s", pos.ticket, exc)
            continue

        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(
                "Trailing stop aggiornato | Ticket %d | Nuovo SL: %.5f",
                pos.ticket,
                new_sl,
            )
            send_telegram(
                tg_token, tg_chat,
                f"🔁 Trailing stop aggiornato\n"
                f"Ticket: {pos.ticket} | {direction}\n"
                f"Prezzo corrente: {pos.price_current:.5f}\n"
                f"Nuovo SL: {new_sl:.5f}",
            )
        else:
            log.warning(
                "Trailing stop fallito | Ticket %d | retcode: %s",
                pos.ticket,
                result.retcode if result else "None",
            )


def open_trade(
    symbol: str,
    order_type: int,
    lot: float,
    sl_price: float,
    tp_price: float,
    deviation: int = 20,
) -> bool:
    try:
        info = mt5.symbol_info(symbol)
        if info is None:
            log.error("symbol_info None per %s. Errore MT5: %s", symbol, mt5.last_error())
            return False

        digits = info.digits

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            log.error("symbol_info_tick None per %s. Errore MT5: %s", symbol, mt5.last_error())
            return False

        if order_type == mt5.ORDER_TYPE_BUY:
            price = tick.ask
        else:
            price = tick.bid

        sl_price = round(sl_price, digits)
        tp_price = round(tp_price, digits)
        price = round(price, digits)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": sl_price,
            "tp": tp_price,
            "deviation": deviation,
            "magic": MAGIC_NUMBER,
            "comment": "GoldBot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        filling_modes = [
            (mt5.ORDER_FILLING_IOC, "ORDER_FILLING_IOC"),
            (mt5.ORDER_FILLING_FOK, "ORDER_FILLING_FOK"),
            (mt5.ORDER_FILLING_RETURN, "ORDER_FILLING_RETURN"),
        ]

        result = None
        for filling, filling_name in filling_modes:
            request["type_filling"] = filling
            try:
                result = mt5.order_send(request)
            except Exception as exc:
                log.error("Eccezione in order_send (%s): %s", filling_name, exc)
                return False

            if result is None:
                log.error(
                    "order_send ha restituito None (%s). Errore MT5: %s",
                    filling_name,
                    mt5.last_error(),
                )
                return False

            if result.retcode == mt5.TRADE_RETCODE_DONE:
                order_dir = _ORDER_DIR.get(order_type, str(order_type))
                log.info(
                    "Ordine %s eseguito con %s | Ticket: %s | Price: %.5f | SL: %.5f | TP: %.5f | Volume: %.2f",
                    order_dir,
                    filling_name,
                    result.order,
                    price,
                    sl_price,
                    tp_price,
                    lot,
                )
                return True

            if result.retcode == mt5.TRADE_RETCODE_INVALID_FILL:
                log.warning(
                    "%s rifiutato (INVALID_FILL), provo prossimo filling mode",
                    filling_name,
                )
                continue

            # Qualsiasi altro errore: interrompi subito
            break

    except Exception as exc:
        log.error("Eccezione in open_trade: %s", exc)
        return False

    log.error(
        "order_send fallito | retcode: %s | Errore MT5: %s",
        result.retcode if result is not None else "None",
        mt5.last_error(),
    )
    return False
