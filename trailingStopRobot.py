import json
import math
import time
import threading
import numpy as np
from binance.spot import Spot as Client
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("API_KEY")
private_key_path = "private_key.pem"

with open(private_key_path, 'rb') as f:
    private_key = f.read()

client = Client(api_key=api_key, private_key=private_key)

moedas = ["RENDER", "NEAR", "SOL", "DOT", "ARB", "AVAX", "LINK", "INJ", "STX", "AAVE"]
max_usdt_por_operacao = 100
trailing_stop_percent = 1.5
estado_arquivo = "estado_trading.json"
MIN_SALDO = 0.01

lock = threading.Lock()

def salvar_estado(estado):
    with open(estado_arquivo, "w") as f:
        json.dump(estado, f, indent=4)

def carregar_estado():
    try:
        with open(estado_arquivo, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"totais": {"ganhos_acumulados": 0, "total_compras": 0, "total_vendas": 0}}

def obter_saldo(moeda):
    conta = client.account()
    saldo_moeda = 0
    saldo_usdt = 0
    for ativo in conta['balances']:
        if ativo['asset'] == moeda:
            saldo_moeda = float(ativo['free'])
        if ativo['asset'] == "USDT":
            saldo_usdt = float(ativo['free'])
    return saldo_moeda, saldo_usdt

def obter_preco_atual(symbol):
    response = client.ticker_price(symbol)
    return float(response['price'])

def obter_historico(symbol, interval="1h", limit=100):
    klines = client.klines(symbol, interval=interval, limit=limit)
    return [float(candle[4]) for candle in klines]

def calcular_rsi(precos, periodos=14):
    deltas = np.diff(precos)
    ganhos = deltas.clip(min=0)
    perdas = -deltas.clip(max=0)
    avg_ganhos = np.convolve(ganhos, np.ones(periodos) / periodos, mode="valid")
    avg_perdas = np.convolve(perdas, np.ones(periodos) / periodos, mode="valid")
    rs = avg_ganhos / avg_perdas
    rsi = 100 - (100 / (1 + rs))
    return rsi[-1]

def ajustar_quantidade(quantidade, step_size):
    quantidade_ajustada = math.floor(quantidade / step_size) * step_size
    decimais_permitidos = abs(math.floor(math.log10(step_size)))
    return round(quantidade_ajustada, decimais_permitidos)

def obter_lotes(symbol):
    info = client.exchange_info(symbol=symbol)
    filters = {f["filterType"]: f for f in info["symbols"][0]["filters"]}
    lot_size = filters["LOT_SIZE"]
    min_qty = float(lot_size["minQty"])
    step_size = float(lot_size["stepSize"])
    return min_qty, step_size

def atualizar_totais(estado, ganhos, compras, vendas):
    with lock:
        estado["totais"]["ganhos_acumulados"] += ganhos
        estado["totais"]["total_compras"] += compras
        estado["totais"]["total_vendas"] += vendas
        salvar_estado(estado)
        log_mensagem("Totais atualizados", estado["totais"])

def log_mensagem(titulo, conteudo):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {titulo}: {conteudo}\n", flush=True)

def monitorar_e_operar(moeda, estado):
    symbol = f"{moeda}USDT"
    log_mensagem(f"{moeda}", "Monitoramento iniciado.")
    historico = estado.get(moeda, {}).get("historico", obter_historico(symbol))
    suporte = estado.get(moeda, {}).get("suporte", min(historico))
    resistencia = estado.get(moeda, {}).get("resistencia", max(historico))
    preco_base = estado.get(moeda, {}).get("preco_base", obter_preco_atual(symbol))
    trailing_stop = estado.get(moeda, {}).get("trailing_stop", None)
    ganhos_acumulados = estado.get(moeda, {}).get("ganhos_acumulados", 0)
    total_compras = estado.get(moeda, {}).get("total_compras", 0)
    total_vendas = estado.get(moeda, {}).get("total_vendas", 0)

    while True:
        try:
            preco_atual = obter_preco_atual(symbol)
            saldo_moeda, saldo_usdt = obter_saldo(moeda)
            log_mensagem(moeda, f"Preço atual: {preco_atual}, Saldo moeda: {saldo_moeda}, Saldo USDT: {saldo_usdt}")

            historico.append(preco_atual)
            if len(historico) > 100:
                historico.pop(0)
            rsi = calcular_rsi(historico)
            log_mensagem(moeda, f"RSI calculado: {rsi}")

            if saldo_moeda > MIN_SALDO:
                novo_stop = preco_atual * (1 - trailing_stop_percent / 100)
                if trailing_stop is None or novo_stop > trailing_stop:
                    trailing_stop = novo_stop
                    log_mensagem(moeda, f"Trailing stop atualizado: {trailing_stop}")

            if saldo_moeda > MIN_SALDO and preco_atual <= trailing_stop:
                min_qty, step_size = obter_lotes(symbol)
                quantidade_ajustada = ajustar_quantidade(saldo_moeda, step_size)
                if quantidade_ajustada >= min_qty:
                    client.new_order(symbol=symbol, side="SELL", type="MARKET", quantity=quantidade_ajustada)
                    ganho = quantidade_ajustada * preco_atual - quantidade_ajustada * preco_base
                    ganhos_acumulados += ganho
                    total_vendas += 1
                    atualizar_totais(estado, ganho, 0, 1)
                    log_mensagem(moeda, f"Venda realizada: {quantidade_ajustada} a {preco_atual}, Ganho: {ganho}")
                    trailing_stop = None

            if saldo_moeda < MIN_SALDO and rsi < 35 and preco_atual <= suporte:
                min_qty, step_size = obter_lotes(symbol)
                quantidade_compra = min(saldo_usdt / preco_atual, max_usdt_por_operacao / preco_atual)
                quantidade_ajustada = ajustar_quantidade(quantidade_compra, step_size)
                if quantidade_ajustada >= min_qty:
                    client.new_order(symbol=symbol, side="BUY", type="MARKET", quantity=quantidade_ajustada)
                    preco_base = preco_atual
                    total_compras += 1
                    atualizar_totais(estado, 0, 1, 0)
                    log_mensagem(moeda, f"Compra realizada: {quantidade_ajustada} a {preco_atual}")

            estado[moeda] = {
                "historico": historico,
                "suporte": min(historico),
                "resistencia": max(historico),
                "preco_base": preco_base,
                "trailing_stop": trailing_stop,
                "ganhos_acumulados": ganhos_acumulados,
                "total_compras": total_compras,
                "total_vendas": total_vendas,
            }
            salvar_estado(estado)

        except Exception as e:
            log_mensagem(moeda, f"Erro: {e}")

        time.sleep(10)

estado = carregar_estado()
threads = []
for moeda in moedas:
    t = threading.Thread(target=monitorar_e_operar, args=(moeda, estado))
    t.start()
    threads.append(t)

for t in threads:
    t.join()
