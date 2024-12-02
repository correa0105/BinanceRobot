from binance.spot import Spot as Client
import math
import time
import threading
from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("API_KEY")
private_key_path = "private_key.pem"

with open(private_key_path, 'rb') as f:
    private_key = f.read()

client = Client(api_key=api_key, private_key=private_key)

moedas = ["RENDER", "NEAR", "SOL", "DOT", "ARB", "AVAX", "LINK", "INJ", "STX", "AAVE"]
limite_venda = 3
limite_recompra = -5
limite_reset = 2.3
max_usdt_por_operacao = 100

vendas_acumuladas_geral = 0
recompras_acumuladas_geral = 0
lucro_geral = 0.0
lock = threading.Lock()


def obter_preco_atual(symbol):
    response = client.ticker_price(symbol)
    return float(response['price'])


def obter_saldo(moeda):
    conta = client.account()
    saldo_moeda = 0
    saldo_usdt = 0

    for asset in conta['balances']:
        if asset['asset'] == moeda:
            saldo_moeda = float(asset['free'])
        if asset['asset'] == 'USDT':
            saldo_usdt = float(asset['free'])

    return saldo_moeda, saldo_usdt


def calcular_variacao(preco_inicial, preco_atual):
    return ((preco_atual - preco_inicial) / preco_inicial) * 100


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


def resetar_base_variacao(preco_atual, preco_base, limite_reset):
    variacao = calcular_variacao(preco_base, preco_atual)
    if variacao >= limite_reset:
        with lock:
            print(f"[RESET] Preço valorizou {variacao:.2f}% sem recompra. Resetando a base de variação.")
        return True
    return False

def monitorar_e_operar(moeda):
    global vendas_acumuladas_geral, recompras_acumuladas_geral, lucro_geral

    symbol = f"{moeda}USDT"
    preco_base = obter_preco_atual(symbol)
    preco_compra = preco_base

    with lock:
        print(f"[{moeda}] Iniciando monitoramento a partir de {preco_base:.2f} USDT")

    min_qty, step_size = obter_lotes(symbol)
    lucro_acumulado = 0
    contador_vendas = 0
    contador_recompras = 0

    while True:
        preco_atual = obter_preco_atual(symbol)
        saldo_moeda, saldo_usdt = obter_saldo(moeda)
        variacao_preco = calcular_variacao(preco_base, preco_atual)

        with lock:
            print(f"""
            [{moeda}] Preço atual: {preco_atual:.2f} USDT
            Variação: {variacao_preco:.2f}%
            Saldo: {saldo_moeda:.4f} {moeda} (${saldo_moeda * preco_atual:.2f} em USDT)
            Saldo USDT disponível: ${saldo_usdt:.2f}
            Lucro acumulado: ${lucro_acumulado:.2f}
            Vendas realizadas: {contador_vendas}
            Recompras realizadas: {contador_recompras}
            """)

        if saldo_moeda == 0 and resetar_base_variacao(preco_atual, preco_base, limite_reset):
            preco_base = preco_atual

        if saldo_moeda > 0 and variacao_preco >= limite_venda:
            with lock:
                print(f"[{moeda}] Condição de VENDA detectada: Preço +{variacao_preco:.2f}%")
            saldo_moeda_ajustado = ajustar_quantidade(saldo_moeda, step_size)
            if saldo_moeda_ajustado >= min_qty:
                lucro_venda = (preco_atual - preco_compra) * saldo_moeda_ajustado
                lucro_acumulado += lucro_venda
                contador_vendas += 1

                with lock:
                    vendas_acumuladas_geral += 1
                    lucro_geral += lucro_venda
                    preco_compra = preco_atual
                    preco_base = preco_compra

                client.new_order(symbol=symbol, side="SELL", type="MARKET", quantity=saldo_moeda_ajustado)

        if saldo_moeda * preco_atual < 5 and variacao_preco <= limite_recompra:
            with lock:
                print(f"[{moeda}] Condição de COMPRA detectada: Preço {variacao_preco:.2f}%")
            quantidade_compra_maxima = min(saldo_usdt / preco_atual, max_usdt_por_operacao / preco_atual)
            quantidade_compra_ajustada = ajustar_quantidade(quantidade_compra_maxima, step_size)

            if quantidade_compra_ajustada < min_qty:
                with lock:
                    print(f"[{moeda}] Quantidade ajustada ({quantidade_compra_ajustada}) menor que mínimo permitido ({min_qty}).")
                continue

            try:
                client.new_order(symbol=symbol, side="BUY", type="MARKET", quantity=quantidade_compra_ajustada)
                with lock:
                    preco_compra = preco_atual
                    preco_base = preco_compra
                    contador_recompras += 1
                    recompras_acumuladas_geral += 1
            except Exception as e:
                with lock:
                    print(f"[{moeda}] Erro ao realizar a compra: {e}")

        with lock:
            print(f"=== Métricas Gerais ===")
            print(f"Vendas acumuladas: {vendas_acumuladas_geral}")
            print(f"Recompras acumuladas: {recompras_acumuladas_geral}")
            print(f"Lucro geral: ${lucro_geral:.2f}")

        time.sleep(10)

threads = []
for moeda in moedas:
    t = threading.Thread(target=monitorar_e_operar, args=(moeda,))
    t.start()
    threads.append(t)

for t in threads:
    t.join()
