# Gold Bot — Trading Bot XAUUSD H1

Bot algoritmico per il trading automatico sull'oro (XAUUSD) su timeframe H1, basato su MetaTrader 5. Supporta modalità dry-run per testare la strategia senza inviare ordini reali.

---

## Strategia

### Logica di entry

La strategia è un sistema di **pullback nel verso del trend principale**:

- **BUY**: prezzo > EMA200 (uptrend) E EMA50 > prezzo (pullback sotto EMA50) E nessuna posizione aperta
- **SELL**: prezzo < EMA200 (downtrend) E EMA50 < prezzo (rimbalzo sopra EMA50) E nessuna posizione aperta

In entrambi i casi il segnale viene filtrato dall'**ADX** (se abilitato): il trade si apre solo se ADX >= soglia, evitando mercati laterali.

### Stop Loss e Take Profit

Calcolati dinamicamente tramite ATR (Average True Range):

```
SL = ATR × 1.5   →   distanza dal prezzo di entry
TP = ATR × 3.0   →   rapporto rischio/rendimento 1:2
```

### Gestione della posizione aperta

Una volta aperto il trade, il bot esegue automaticamente (ad ogni candela):

1. **Breakeven**: quando il profitto raggiunge 1R, lo SL viene spostato all'entry — il trade non può più chiudersi in perdita
2. **Trailing stop**: quando il profitto raggiunge 1R, lo SL segue il prezzo a 0.75× ATR di distanza — lascia correre i vincitori

Le due logiche sono indipendenti e configurabili separatamente.

---

## Funzionalità

| Funzione | Descrizione |
|---|---|
| Dry run | Simula i segnali senza inviare ordini; logga e notifica come se fossero reali |
| Position sizing dinamico | Il lot è calcolato per rischiare una % fissa del saldo per trade |
| Filtro ADX | Blocca i segnali quando il mercato è laterale (ADX < soglia) |
| Breakeven automatico | SL → entry quando profitto >= 1R |
| Trailing stop | SL segue il prezzo a distanza fissa (in ATR) |
| Max trade/giorno | Limite giornaliero per evitare overtrading |
| Filtro sessione | Opera solo in finestra oraria configurabile (default 8-17 UTC) |
| Pausa pranzo | Esclude un'ora di bassa liquidità (default 12-13 UTC) |
| Drawdown hard stop | Ferma il bot se il saldo scende sotto una soglia (default -20%) |
| Reconnect automatico | Se MT5 si disconnette, tenta la riconnessione fino a 3 volte |
| Notifica chiusura trade | Telegram con P&L netto (profit + commissione + swap) per ogni trade chiuso |
| Report giornaliero | Inviato a fine sessione con trade count, P&L del giorno e saldo |
| Filling mode fallback | Prova automaticamente IOC → FOK → RETURN se il broker rifiuta il tipo |
| Notifiche Telegram | Avvio bot, segnali, trailing, breakeven, disconnessione, arresto |

---

## Struttura file

```
gold-bot/
├── bot.py              # Loop principale, logica segnali, orchestrazione
├── utils.py            # Funzioni: indicatori, ordini, trailing, breakeven, Telegram
├── logger_setup.py     # Configurazione logging su file e console
├── config.json         # Parametri operativi (NON committare — contiene credenziali)
├── config.example.json # Template config senza credenziali
├── requirements.txt    # Dipendenze Python
└── README.md           # Questo file
```

---

## Parametri config.json

### Strategia

| Parametro | Default | Descrizione |
|---|---|---|
| `symbol` | `XAUUSD` | Simbolo da tradare |
| `timeframe` | `H1` | Timeframe (M1, M5, M15, M30, H1, H4, D1) |
| `ema_slow` | `200` | Periodo EMA trend lento |
| `ema_fast` | `50` | Periodo EMA trend rapido |
| `atr_period` | `14` | Periodo ATR (Wilder's smoothing) |
| `atr_mult_sl` | `1.5` | Moltiplicatore ATR per SL |
| `atr_mult_tp` | `3.0` | Moltiplicatore ATR per TP |

### Dimensionamento posizione

| Parametro | Default | Descrizione |
|---|---|---|
| `risk_pct` | `0.01` | Rischio per trade come % del saldo (1% = 0.01) |
| `min_lot` | `0.01` | Lot minimo |
| `max_lot` | `1.0` | Lot massimo |

### Gestione posizione aperta

| Parametro | Default | Descrizione |
|---|---|---|
| `breakeven_enabled` | `true` | Attiva breakeven automatico |
| `breakeven_activation_r` | `1.0` | Profitto minimo (in R) per attivare il breakeven |
| `trail_enabled` | `true` | Attiva trailing stop |
| `trail_activation_r` | `1.0` | Profitto minimo (in R) per attivare il trailing |
| `trail_offset_atr` | `0.75` | Distanza trailing dallo SL (in ATR) |

### Filtri

| Parametro | Default | Descrizione |
|---|---|---|
| `adx_filter_enabled` | `true` | Attiva filtro ADX |
| `adx_period` | `14` | Periodo ADX |
| `adx_threshold` | `25.0` | ADX minimo per aprire un trade |
| `max_daily_trades` | `3` | Numero massimo di trade al giorno |
| `drawdown_limit` | `0.80` | Ferma il bot se saldo < X% del saldo iniziale (0.80 = -20%) |

### Sessione

| Parametro | Default | Descrizione |
|---|---|---|
| `session_start_utc` | `8` | Ora UTC di inizio sessione |
| `session_end_utc` | `17` | Ora UTC di fine sessione |
| `lunch_break_start_utc` | `12` | Inizio pausa pranzo (UTC) |
| `lunch_break_end_utc` | `13` | Fine pausa pranzo (UTC) |

### Connessione

| Parametro | Descrizione |
|---|---|
| `mt5_login` | Login MetaTrader 5 |
| `mt5_password` | Password MetaTrader 5 |
| `mt5_server` | Server broker (es. `ICMarketsEU-Demo`) |
| `telegram_token` | Token bot Telegram (da @BotFather) |
| `telegram_chat_id` | ID chat Telegram (da @userinfobot) |
| `dry_run` | `true` = nessun ordine reale, `false` = trading live |

---

## Installazione

```bash
pip install -r requirements.txt
```

Dipendenze: `MetaTrader5`, `pandas`, `numpy`, `requests`

### Configurazione

```bash
cp config.example.json config.json
# Edita config.json con le tue credenziali MT5 e Telegram
```

### Avvio

```bash
python bot.py
```

---

## Notifiche Telegram

| Evento | Emoji |
|---|---|
| Bot avviato (con riepilogo parametri) | 🤖 |
| Segnale BUY/SELL (dry run) | 🟡 |
| Ordine BUY/SELL aperto (live) | ✅ |
| Breakeven impostato | 🔒 |
| Trailing stop aggiornato | 🔁 |
| Trade chiuso in profitto | ✅ |
| Trade chiuso in perdita | ❌ |
| Report giornaliero | 📈 / 📉 |
| MT5 disconnesso | ⚠️ |
| MT5 riconnesso | 🟢 |
| Hard stop drawdown | 🛑 |
| Bot fermato | 🔴 |

---

## Flusso di esecuzione (ogni candela H1)

```
1. Check connessione MT5 → reconnect se disconnesso
2. Check drawdown → hard stop se superato
3. Check trade chiusi → notifica P&L su Telegram
4. Check report giornaliero → invia a fine sessione (17 UTC)
5. Check sessione e pausa pranzo → sleep se fuori orario
6. Fetch 300 candele da MT5
7. Check nuova candela → skip se stessa candela
8. Calcola EMA50, EMA200, ATR, ADX
9. Calcola lot size dinamico (rischio % fisso)
10. Breakeven sulle posizioni aperte (se attivo)
11. Trailing stop sulle posizioni aperte (se attivo)
12. Valuta segnale BUY/SELL (con filtri ADX e max trade/giorno)
13. Se segnale: ottieni tick, calcola SL/TP, apri ordine (o logga in dry run)
14. Notifica Telegram
15. Sleep fino alla prossima candela
```
