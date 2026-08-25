import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import requests
import time
import io, os, sys
import threading
import comtradeapicall   
from collections import deque

# ── Defaults globales ─────────────────────────────────────────────────────────
PRODUCTOS_DEFAULT = ['Maiz', 'Sorgo', 'Alfalfa']

def _sufijos_auto(productos):
    """Genera sufijos únicos para cada producto, evitando choques con C/U/M/W/T."""
    usados = {'C', 'U', 'M', 'W', 'T'}
    sufijos = {}
    for p in productos:
        for n in range(1, len(p) + 1):
            candidato = p[:n]
            if candidato not in usados:
                sufijos[p] = candidato
                usados.add(candidato)
                break
    sufijos['Total'] = 'T'
    return sufijos

SUFIJOS_DEFAULT = _sufijos_auto(PRODUCTOS_DEFAULT)

#-------------------------------------------------------------------------------------------------------------------------------
def calcular_valor_real(df, nombre, año_base):
    df['Año'] = pd.to_numeric(df['Año'], errors='coerce')
    df['precio_implicito'] = df['Valor Total (USD)'] / df['Cantidad Total (toneladas)']
    if año_base not in df['Año'].values:
        raise ValueError(f"El año base {año_base} no está presente en el DataFrame {nombre}.")
    else:   
        precio_base = df[df['Año'] == año_base]['precio_implicito'].values[0]
        df['valor_real'] = df['Cantidad Total (toneladas)'] * precio_base
        df['valor_real en miles'] = df['valor_real'] / 1000
        df.drop(columns = 'precio_implicito', inplace =True)
        return df

def calcular_valor_real_ipc(df, nombre, ipc, año, col_ipc_año='Año', col_ipc_indice='IPC'):

    df['Año'] = df['Año'].astype(int)

    if col_ipc_indice in df.columns:
        df.drop(columns=[col_ipc_indice], inplace=True)

    if isinstance(ipc, dict):
        ipc_dict = ipc
    elif isinstance(ipc, pd.DataFrame):
        ipc_dict = dict(zip(ipc[col_ipc_año].astype(int), ipc[col_ipc_indice]))
    elif isinstance(ipc, str):
        ipc_df = pd.read_excel(ipc) if ipc.endswith('.xlsx') else pd.read_csv(ipc)
        ipc_dict = dict(zip(ipc_df[col_ipc_año].astype(int), ipc_df[col_ipc_indice]))
    else:
        raise ValueError('ipc debe ser dict, DataFrame o ruta')
    

    # Validar que el año base esté en el IPC, no en el df
    if año not in ipc_dict:
        raise ValueError(f'El año base {año} no está en el IPC')

    df[col_ipc_indice] = df['Año'].map(ipc_dict)

    sin_ipc = df[df[col_ipc_indice].isna()]['Año'].unique()
    if len(sin_ipc) > 0:
        print(f'⚠️ {nombre}: años sin IPC → {sin_ipc}')

    ipc_base = ipc_dict[año]
    df[col_ipc_indice] = df[col_ipc_indice] / ipc_base
    df['valor_real_ipc'] = df['Valor Total (USD)'] / df[col_ipc_indice]
    df['valor_real_ipc en miles'] = df['valor_real_ipc'] / 1000
    df.drop(columns=[col_ipc_indice], inplace=True)

    return df

def mapeo(dfs):
    for df in dfs:
        # Eliminar columnas Unnamed
        cols_unnamed = [c for c in df.columns if str(c).startswith('Unnamed')]
        if cols_unnamed:
            df.drop(columns=cols_unnamed, inplace=True)
        df.rename(columns={
            'toneladas_total': 'Cantidad Total (toneladas)',
            'year': 'Año',
            'valor_usd': 'Valor Total (USD)',
            'valor_total': 'Valor Total (USD)',
            'Toneladas': 'Cantidad Total (toneladas)'
        }, inplace=True)

def valor_real(dfs, nombres, min_años, ipc_path=None, col_ipc_año='Año', col_ipc_indice='IPC'):
    """
    Calcula el valor real usando precio implícito y/o IPC.

    Parámetros:
    -----------
    dfs           : list — lista de DataFrames
    nombres       : list — lista de nombres
    min_años      : int  — mínimo de años requeridos
    ipc_path      : str  — ruta al CSV/xlsx del IPC (opcional)
    col_ipc_año   : str  — columna de años en el IPC (default: 'Año')
    col_ipc_indice: str  — columna del índice en el IPC (default: 'IPC')
    """
    mapeo(dfs)

    # Preguntar qué método usar
    print('¿Qué método de deflación quieres usar?')
    print('  1 — Precio implícito')
    print('  2 — IPC')
    print('  3 — Ambos')
    metodo = input('Elige (1/2/3, default = 3): ').strip()
    if metodo not in ['1', '2', '3']:
        metodo = '3'

    # Leer IPC si se necesita
    # Leer IPC si se necesita
    ipc = None
    ipc_dict = None
    if metodo in ['2', '3']:
        if ipc_path is None:
            raise ValueError('Se necesita ipc_path para usar el método IPC')
        ipc = pd.read_excel(ipc_path) if ipc_path.endswith('.xlsx') else pd.read_csv(ipc_path)
        ipc[col_ipc_año] = ipc[col_ipc_año].astype(int)
        ipc_dict = dict(zip(ipc[col_ipc_año], ipc[col_ipc_indice]))

    # Años comunes solo si se necesita precio implícito
    lista_años = None
    if metodo in ['1', '3']:
        dfs_filtrados = [(df, n) for df, n in zip(dfs, nombres) if df['Año'].nunique() >= min_años]
        años_comunes = set(dfs_filtrados[0][0]['Año'])
        for df, nombre in dfs_filtrados[1:]:
            nueva_interseccion = años_comunes & set(df['Año'])
            if nueva_interseccion:
                años_comunes = nueva_interseccion
            else:
                print(f"⚠️ '{nombre}' vacía la intersección — excluido.")
        lista_años = sorted(años_comunes)
        print(f'\nAños disponibles en común: {lista_años}')

    # Elegir año base
    if lista_años:
        año_elegido = input(f'Elige el año base (default = {lista_años[0]}): ').strip()
        if año_elegido == '' or not año_elegido.isdigit():
            año_elegido = lista_años[0]
        else:
            año_elegido = int(año_elegido)
            if año_elegido not in lista_años:
                print(f'⚠️ Año {año_elegido} no está en los años comunes, usando {lista_años[0]}')
                año_elegido = lista_años[0]
    else:
        # Solo IPC — cualquier año del IPC es válido
        años_ipc = sorted(ipc_dict.keys())
        año_elegido = input(f'Elige el año base del IPC (default = {años_ipc[-1]}): ').strip()
        if año_elegido == '' or not año_elegido.isdigit():
            año_elegido = años_ipc[-1]
        else:
            año_elegido = int(año_elegido)
            if año_elegido not in años_ipc:
                print(f'⚠️ Año {año_elegido} no está en el IPC, usando {años_ipc[-1]}')
                año_elegido = años_ipc[-1]

    print(f'Año base elegido: {año_elegido}')

    dfs_cumplenstr = []
    dfCumplen = []

    for i, (df, nombre) in enumerate(zip(dfs, nombres)):
        if df['Año'].nunique() < min_años:
            print(f"El DataFrame {nombre} tiene menos de {min_años} años y no se incluirá.")
            continue
        if 'Valor Total (USD)' not in df.columns:
            print(f'{nombre}: No tiene la columna Valor Total (USD)')
            continue
        if 'Cantidad Total (toneladas)' not in df.columns:
            print(f'{nombre}: No tiene la columna Cantidad Total (toneladas)')
            continue
        if metodo in ['1', '3'] and set(df['Año']).isdisjoint(años_comunes):
            print(f"⚠️ '{nombre}' no tiene ningún año en común.")
            continue
    
        df.drop(df[df['Año'] < 1993].index, inplace=True)
                  # ← actualiza la lista original
    
        if metodo in ['1', '3']:
            calcular_valor_real(df, nombre, año_elegido)
        if metodo in ['2', '3']:
            calcular_valor_real_ipc(df, nombre, ipc_dict, año_elegido, col_ipc_año, col_ipc_indice)
    
        dfs_cumplenstr.append(nombre)
        dfCumplen.append(df)
    
    print(f'{"="*50}\nAño base elegido: {año_elegido}\n{"="*50}')
    for i, df in zip(dfs_cumplenstr, dfCumplen):
        tiene_pi = 'valor_real en miles' in df.columns
        tiene_ipc = 'valor_real_ipc en miles' in df.columns
        print(f"{i}: precio implícito {'✅' if tiene_pi else '❌'} | IPC {'✅' if tiene_ipc else '❌'}")

    return  print(f'DataFrames con valor real calculado:\n{dfs_cumplenstr}')


#---------------------------------------------------------------------------------------------------------------------
def get_cols(df):
    cols = ['Año', 'Cantidad Total (toneladas)']
    if 'valor_real en miles' in df.columns:
        cols.append('valor_real en miles')
    if 'valor_real_ipc en miles' in df.columns:
        cols.append('valor_real_ipc en miles')
    return cols

def concat_sumar(filas):
    tiene_valor = all('valor_real en miles' in f.columns for f in filas)
    tiene_ipc = all('valor_real_ipc en miles' in f.columns for f in filas)
    cols = ['Año', 'Cantidad Total (toneladas)']
    if tiene_valor:
        cols.append('valor_real en miles')
    if tiene_ipc:
        cols.append('valor_real_ipc en miles')
    filas_filtradas = [f[cols] for f in filas]
    return pd.concat(filas_filtradas).groupby('Año').sum().reset_index()

def sumarDfs(nombres, dfs, productos=None, sufijos=None):
    paises = ['C', 'U', 'M']  # Canadá, USA, México, Mundo
    if productos is None:
        productos = PRODUCTOS_DEFAULT
    if sufijos is None:
        sufijos = _sufijos_auto(productos)

    agg = {}  # agg[pais][flujo][producto]
    
    for pais in paises:
        agg[pais] = {
            'E': {p: [] for p in productos + ['Total']},
            'M': {p: [] for p in productos + ['Total']},
            'EW': {p: [] for p in productos + ['Total']},
            'MW': {p: [] for p in productos + ['Total']},
            'P': {p: [] for p in productos + ['Total']}

        }
    
    for nombre, df in zip(nombres,dfs):
        productor = None  # ← inicializar
        exp = None        # ← inicializar
        imp = None 
        if nombre[0] == 'P':
           productor = nombre[1] 
           cols = get_cols(df)
        else:
            exp = nombre[2]  # exportador
            imp = nombre[3]  # importador
            cols = get_cols(df)
        
        for producto in productos + ['Total']:
            if producto != 'Total' and producto not in nombre:
                continue
            if productor:
                agg[productor]['P'][producto].append(df[cols])

            # Exportaciones bilaterales entre USMCA (sin mundo)
            if exp in paises and imp in paises:
                agg[exp]['E'][producto].append(df[cols])
                agg[imp]['M'][producto].append(df[cols])
            
            # Exportaciones al mundo (exportador=pais, importador=W)
            if exp in paises and imp == 'W':
                agg[exp]['EW'][producto].append(df[cols])
            
            # Importaciones del mundo (exportador=W, importador=pais)
            if exp == 'W' and imp in paises:
                agg[imp]['MW'][producto].append(df[cols])


    # Crear DataFrames finales
    resultado = {}
    nombres_df = []
    for pais in paises:
        for flujo in ['E','M', 'EW', 'MW', 'P']:
            for producto, suf in sufijos.items():
                filas = agg[pais][flujo][producto]
                if filas:
                    varname = f'df{flujo}{suf}_{pais}'
                    resultado[varname] = concat_sumar(filas)  # ← diccionario
                    nombres_df.append(varname)   
    return resultado  
#----------------------------------------------------------------------------------------------------------

def get_flujo(pais):
    return {
        'CU': 'CA → USA',
        'UM': 'USA → MX',
        'MU': 'MX → USA',
        'MC': 'MX → CA',
        'UC': 'USA → CA',
        'CM': 'CA → MX',
        'CW': 'CA → WORLD',
        'MW': 'MX → WORLD',
        'UW': 'USA → WORLD',
        'WC': 'WORLD → CA',
        'WM': 'WORLD → MX',
        'WU': 'WORLD → USA',
        "EWT_C" :'Exportaciones totales de CA',
        'EWT_M' :'Exportaciones totales de MX',
        'EWT_U' :"Exportaciones totales de USA",
        "MWT_C" :'Importaciones totales de CA',
        'MWT_M' :'Importaciones totales de MX',
        'MWT_U' :"Importaciones totales de USA",
        "ET_C" :'Exportaciones totales de CA',
        'ET_M' :'Exportaciones totales de MX',
        'ET_U' :"Exportaciones totales de USA",
        "MT_C" :'Importaciones totales de CA',
        'MT_M' :'Importaciones totales de MX',
        'MT_U' :"Importaciones totales de USA",
        'C'    :'Canadá',
        'U'    :'EE.UU.',
        'M'    :'México',
        'P'    :'Produccion'
    }.get(pais, pais)

def colu_boni(h):
    return {
        # columnas originales
        'Cantidad Total (toneladas)' : 'Toneladas metricas',
        'valor_real_ipc en miles'    : 'Valor',
        # productos
        'Maiz'    : 'M',
        'Sorgo'   : 'S',
        'Alfalfa' : 'A',
        'T'       : 'Total',
        # sufijos de métrica usados internamente
        'ton' : 'Ton',
        'ipc' : 'Valor',
        # Lafay
        'Lafay_ton' : 'Lafay Ton',
        'Lafay_ipc' : 'Lafay Valor',
        # Krugman
        'IK' : 'IK',
        # Concentración
        'Conc_X_ipc' : 'Conc. X',
        'Conc_M_ipc' : 'Conc. M',
        # Grubel-Lloyd
        'IGL' : 'IGL',
        # Balance / Consumo / Dependencia
        'Balanza_ton'     : 'Balanza Ton',
        'Balanza_ipc'     : 'Balanza Valor',
        'Consumo_ton'     : 'Consumo Ton',
        'Consumo_ipc'     : 'Consumo Valor',
        'Dependencia_ton' : 'Dep. Ton',
        'Dependencia_ipc' : 'Dep. Valor',
    }.get(h, h)
 


def smart_formatter(ax, values):
    vals = [v for v in values if v is not None and not pd.isna(v)]
    if not vals: return
    max_val = max(abs(v) for v in vals)
    
    if max_val >= 1e6:
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{x/1e6:.1f}M'))
    elif max_val >= 1e4:
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{x/1e4:.1f}K'))
    else:
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{x:.1f}'))

def grafica_producto(dfs, nombres, columna, producto):
    if not all(isinstance(df, pd.DataFrame) for df in dfs):
        raise ValueError('Primero introduzca los DataFrames')
    if not isinstance(nombres, list):
        raise ValueError('El segundo parámetro tiene que ser una lista de nombres')
    if not isinstance(producto, str):
        raise ValueError('El último parámetro es el producto como string')
    dfP=[]
    dft = []
    for df, nombre in zip(dfs, nombres):
        if nombre[:1] == 'P':
            flujo = nombre[1:2]
            df['Flujo'] = get_flujo(flujo)
            dfP.append(df)
        else:   
            flujo = nombre[2:4]  # extrae 'CU' de 'dfCUAlfalfa'
            df['Flujo'] = get_flujo(flujo)
            dft.append(df)
    df_total = pd.concat(dft, ignore_index=True)
    dfproduccion = pd.concat(dfP, ignore_index=True)
    if columna == 1:
        columnas = ['Cantidad Total (toneladas)']
    elif columna == 2:
        columnas = ['valor_real en miles']
    elif columna == 3:
        columnas = ['Cantidad Total (toneladas)', 'valor_real en miles']
    elif columna == 4:
        columnas = ['Cantidad Total (toneladas)', 'valor_real_ipc en miles']
    else:
        raise ValueError("columna debe ser 1, 2 o 3")
    resultado = sumarDfs(nombres, dfs) 
    productosuma= colu_boni(producto)

    ordenE = [f'dfEW{productosuma}_C', f'dfEW{productosuma}_M', f'dfEW{productosuma}_U']
    ordenM = [f'dfMW{productosuma}_C', f'dfMW{productosuma}_M', f'dfMW{productosuma}_U']
    dfTotalesE = []
    nombresTotalesE = []
    for k in ordenE:
        pais = k[-1]
        df = resultado.get(f'dfWE{productosuma}_{pais}')
        nombre = f'dfWE{productosuma}_{pais}'
        if df is None:
            df = resultado.get(f'dfE{productosuma}_{pais}')
            nombre = f'dfE{productosuma}_{pais}'
        if df is not None:
            dfTotalesE.append(df)
            nombresTotalesE.append(nombre)
    
    dfTotalesM = []
    nombresTotalesM = []
    for k in ordenM:
        pais = k[-1]
        df = resultado.get(f'dfMW{productosuma}_{pais}')
        nombre = f'dfMW{productosuma}_{pais}'
        if df is None:
            df = resultado.get(f'dfMW{productosuma}_{pais}')
            nombre = f'dfMW{productosuma}_{pais}'
        if df is not None:
            dfTotalesM.append(df)
            nombresTotalesM.append(nombre)

    for g, n in zip(dfTotalesE, nombresTotalesE):
        flujon = n[2:]
        g['Flujo'] = get_flujo(flujon)

    for g, n in zip(dfTotalesM, nombresTotalesM):
        flujon = n[2:]
        g['Flujo'] = get_flujo(flujon)
     # ← lista de dfs 
    dfsdeTotalE = pd.concat(dfTotalesE, ignore_index=True)
    dfsdeTotalM = pd.concat(dfTotalesM, ignore_index=True)
    balance = pd.DataFrame()
    for e, dfe, dfm in zip(nombresTotalesE, dfTotalesE, dfTotalesM):
        pais = get_flujo(e[-1])  # 'C', 'M' o 'U'
        temp = dfe.set_index('Año')[columnas] - dfm.set_index('Año')[columnas]
        temp = temp.reset_index()
        temp['Flujo'] = f'Balance de {pais}'
        balance = pd.concat([balance, temp], ignore_index=True)
    
    # --- Estilo ---
    dfgraf = [df_total, dfproduccion, dfsdeTotalE, dfsdeTotalM, balance]
    sns.set_theme(style='whitegrid', palette='tab10')
    for k in dfgraf:
        for h in columnas:
            fig, ax = plt.subplots(figsize=(12, 6))
        
            
            # --- Gráfica ---
            sns.lineplot(
                data=k,
                x='Año',
                y=h,
                hue='Flujo',
                linewidth=2.5,
                marker='o',
                markersize=5,
                ax=ax
            )
            f = 'Flujo comercial'
            col_original = h
            h = colu_boni(h)
            if k is df_total:
                titulo = f'Exportaciones de {producto} por flujo comercial en {h} (1995–2024)'
            if k is dfsdeTotalE:
                titulo = f'Exportaciones de {producto} por exportaciones totales en {h} (1995–2024)'
            if k is dfsdeTotalM:
                titulo = f'Importaciones de {producto} por importaciones totales en {h} (1995–2024)'
            if k is balance:
                titulo = f'Balance comercial del {producto} en {h} (1995–2024)'
            if k is dfproduccion:
                titulo = f'Produccion del {producto} en {h} (1995–2024)'
                f = 'Produccion de'
             
            # --- Formato ---
            ax.set_title(titulo,
                         fontsize=15, fontweight='bold', pad=15)
            ax.set_xlabel('Año', fontsize=12)
            ax.set_ylabel('Toneladas', fontsize=12)
            smart_formatter(ax, k[col_original].values.flatten().tolist())
            ax.legend(title=f, fontsize=10, title_fontsize=11)
            ax.tick_params(axis='x', rotation=45)            
            plt.tight_layout()
            plt.savefig(f'{titulo}.png',dpi=150)
            plt.show()

#--------------------------------------------
def descargar_comercio_gats(
    hs_codes,
    partner_code,
    ruta,
    nombre_archivo,
    api_keys,
    tipo='M',
    n_meses=12,
    modo='dominante',
    keys_bloqueadas=None,
    lock_keys=None,
    pool_global_keys=None,
):
    _tipo_map = {'M': 'censusImports', 'X': 'censusExports'}
    if tipo not in _tipo_map:
        raise ValueError(f"tipo debe ser 'M' o 'X', recibido: '{tipo}'")
    endpoint = _tipo_map[tipo]

    if pool_global_keys is None:
        pool_global_keys = api_keys

    cache           = {}
    key_index       = [0]
    cache_lock      = threading.Lock()

    def get_mes(year, mes):
        if keys_bloqueadas is not None and lock_keys is not None:
            with lock_keys:
                keys_vivas = [k for k in api_keys if k not in keys_bloqueadas]
                if not keys_vivas:
                    keys_vivas = [k for k in pool_global_keys if k not in keys_bloqueadas]
        else:
            keys_vivas = list(api_keys)

        if not keys_vivas:
            print(f'  {year} mes {mes}: sin keys disponibles')
            return pd.DataFrame()

        for intento in range(len(keys_vivas) * 3):
            api_key = keys_vivas[key_index[0] % len(keys_vivas)]
            try:
                r = requests.get(
                    f'https://api.fas.usda.gov/api/gats/{endpoint}'
                    f'/partnerCode/{partner_code}/year/{year}/month/{mes}'
                    f'?api_key={api_key}',
                    timeout=30
                )
                if r.status_code == 200 and r.json():
                    df = pd.DataFrame(r.json())
                    codigos_col = df['hS10Code'].astype(str)
                    df_filtrado = df[codigos_col.apply(
                        lambda x: any(x.startswith(c) for c in hs_codes)
                    )]
                    return df_filtrado

                elif r.status_code in [429, 403]:
                    print(f'  Key {api_key[:8]}... bloqueada (status {r.status_code}), descartando...')
                    if keys_bloqueadas is not None and lock_keys is not None:
                        with lock_keys:
                            keys_bloqueadas.add(api_key)
                            keys_vivas = [k for k in api_keys if k not in keys_bloqueadas]
                            if not keys_vivas:
                                keys_vivas = [k for k in pool_global_keys if k not in keys_bloqueadas]
                    else:
                        keys_vivas = [k for k in keys_vivas if k != api_key]
                    if not keys_vivas:
                        print(f'  {year} mes {mes}: todas las keys bloqueadas')
                        return pd.DataFrame()
                    key_index[0] += 1
                    time.sleep(1)
                else:
                    return pd.DataFrame()
            except Exception as e:
                print(f'  {year} mes {mes} key {api_key[:8]}... fallido: {e}')
                key_index[0] += 1
                time.sleep(3)

        print(f'  {year} mes {mes}: todas las keys fallaron')
        return pd.DataFrame()

    def get_mes_cache(year, mes):
        with cache_lock:
            if (year, mes) not in cache:
                cache[(year, mes)] = get_mes(year, mes)
                time.sleep(0.3)
            return cache[(year, mes)]

    def verificar_acumulado(year, mes_base):
        df_base = get_mes_cache(year, mes_base)
        if df_base.empty:
            return None, df_base
        qty_base        = df_base['quantity1'].sum()
        suma_anteriores = 0
        meses_con_datos = 0
        for mes_prev in range(mes_base - 1, max(0, mes_base - n_meses - 1), -1):
            if mes_prev < 1:
                break
            df_prev = get_mes_cache(year, mes_prev)
            if df_prev.empty:
                continue
            qty_prev = df_prev['quantity1'].sum()
            suma_anteriores += qty_prev
            meses_con_datos += 1
            print(f'  mes {mes_prev}: {qty_prev:,.0f}')
        print(f'  mes {mes_base}: {qty_base:,.0f} | suma {meses_con_datos} meses anteriores: {suma_anteriores:,.0f}')
        if meses_con_datos == 0:
            return True, df_base
        if meses_con_datos <= 3:
            print(f'{year}: solo {meses_con_datos} meses previos → tratando como mensual')
            return False, df_base
        if qty_base >= suma_anteriores:
            print(f'{year}: mes {mes_base} parece acumulado')
            return True, df_base
        else:
            print(f'{year}: mes {mes_base} ({qty_base:,.0f}) < suma anteriores ({suma_anteriores:,.0f}) → datos mensuales')
            return False, df_base

    def sumar_todos_los_meses(year):
        print(f'{year}: sumando todos los meses individualmente...')
        dfs = []
        for mes in range(1, 13):
            df_mes = get_mes_cache(year, mes)
            if not df_mes.empty:
                df_mes = df_mes.copy()
                df_mes['mes_origen'] = mes
                dfs.append(df_mes)
        return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

    # ── descarga año por año ──────────────────────────────────────────────────
    frames = []
    for year in range(1994, 2026):
        df_year    = pd.DataFrame()
        acumulado, df_12 = verificar_acumulado(year, 12)
        if acumulado is True:
            df_year = df_12
        elif acumulado is False:
            df_year = sumar_todos_los_meses(year)
        else:
            for i in range(11, 0, -1):
                acumulado_i, df_i = verificar_acumulado(year, i)
                if acumulado_i is True:
                    df_year = df_i
                    break
                elif acumulado_i is False:
                    df_year = sumar_todos_los_meses(year)
                    break

        if not df_year.empty:
            frames.append(df_year)
            print(f'{year}: {len(df_year)} registros')
        else:
            print(f'{year}: sin datos')

    if not frames:
        print('Sin datos para el periodo seleccionado.')
        return pd.DataFrame()

    # ── post-proceso ──────────────────────────────────────────────────────────
    df_final         = pd.concat(frames, ignore_index=True)
    df_final['year'] = df_final['date'].astype(str).str[:4]

    ruta_raw = os.path.join(ruta, nombre_archivo)
    df_final.to_csv(ruta_raw, index=False)
    print(f'\nRaw guardado en: {ruta_raw}')

    resumen = pd.read_csv(ruta_raw)
    resumen['Cantidad Total (toneladas)'] = resumen['quantity1'].astype(float)
    resumen['Valor Total (USD)']          = resumen['value'].astype(float)

    for index, row in resumen.iterrows():
        if row['censusUOMId1'] != row['fasNonConvertedUOMId']:
            if row['censusUOMId1'] == 47:
                resumen.at[index, 'Cantidad Total (toneladas)'] = row['Cantidad Total (toneladas)'] / 1000
            elif row['censusUOMId1'] == 70:
                resumen.at[index, 'Cantidad Total (toneladas)'] = row['Cantidad Total (toneladas)'] * 1000

    resumen_original= resumen.copy()
    
    detalle_codigos = resumen.groupby(['year', 'hS10Code']).agg(
        registros=('hS10Code',                    'count'),
        cantidad =('Cantidad Total (toneladas)',   'sum'),
        valor    =('Valor Total (USD)',            'sum')
    ).reset_index().rename(columns={
        'year':      'Año',
        'hS10Code':  'Código HS',
        'registros': 'Registros',
        'cantidad':  'Cantidad (toneladas)',
        'valor':     'Valor (USD)'
    })
    
    # ── aplicar modo elegido ──────────────────────────────────────────────────
    if modo == 'suma':
        print('\n📊 Modo: sumando todos los códigos por año')
        resumen_agrupado = resumen.groupby('year').agg(
            cantidad_total=('Cantidad Total (toneladas)', 'sum'),
            valor_total   =('Valor Total (USD)',          'sum')
        ).reset_index().rename(columns={
            'year':           'Año',
            'cantidad_total': 'Cantidad Total (toneladas)',
            'valor_total':    'Valor Total (USD)'
        })

    else:  # dominante
        print('\n📊 Modo: código dominante por año')
        idx_max          = detalle_codigos.groupby('Año')['Cantidad (toneladas)'].idxmax()
        codigo_dominante = detalle_codigos.loc[idx_max][['Año', 'Código HS']].copy()

        codigos_descartados = detalle_codigos[~detalle_codigos.index.isin(idx_max)]
        if not codigos_descartados.empty:
            for _, row in codigos_descartados.iterrows():
                print(f'  ⚠️ {row["Año"]}: descartando {row["Código HS"]} '
                      f'({row["Cantidad (toneladas)"]:.0f} ton)')

        resumen['year_str']              = resumen['year'].astype(str)
        codigo_dominante['Año_str']      = codigo_dominante['Año'].astype(str)
        codigo_dominante['hS10Code_dom'] = codigo_dominante['Código HS'].astype(str)

        resumen = resumen.merge(
            codigo_dominante[['Año_str', 'hS10Code_dom']],
            left_on='year_str', right_on='Año_str', how='inner'
        )
        resumen = resumen[resumen['hS10Code'].astype(str) == resumen['hS10Code_dom']]
        resumen = resumen.drop(columns=['year_str', 'Año_str', 'hS10Code_dom'], errors='ignore')

        resumen_agrupado = resumen.groupby('year').agg(
            cantidad_total=('Cantidad Total (toneladas)', 'sum'),
            valor_total   =('Valor Total (USD)',          'sum')
        ).reset_index().rename(columns={
            'year':           'Año',
            'cantidad_total': 'Cantidad Total (toneladas)',
            'valor_total':    'Valor Total (USD)'
        })
        detalle_codigos = detalle_codigos.loc[idx_max].reset_index(drop=True)

    # ── hoja de códigos por año — lista los códigos reales ───────────────────
    detalle_completo = resumen_original.groupby(['year', 'hS10Code']).agg(
        cantidad=('Cantidad Total (toneladas)', 'sum'),
        valor   =('Valor Total (USD)',          'sum')
    ).reset_index()

    codigos_por_año = detalle_completo.groupby('year').agg(
        codigos_usados =('hS10Code', lambda x: ', '.join(sorted(x.astype(str).unique()))),
        n_codigos      =('hS10Code', 'nunique'),
        cantidad_total =('cantidad', 'sum'),
        valor_total    =('valor',    'sum')
    ).reset_index().rename(columns={
        'year':           'Año',
        'codigos_usados': 'Códigos HS usados',
        'n_codigos':      'N° códigos',
        'cantidad_total': 'Cantidad Total (toneladas)',
        'valor_total':    'Valor Total (USD)'
    })

    # ── lista global de todos los códigos usados (una sola fila) ─────────────
    todos_codigos_usados = sorted(detalle_completo['hS10Code'].astype(str).unique())
    lista_global = pd.DataFrame({
        'Todos los Códigos HS con datos': [', '.join(todos_codigos_usados)],
        'Total códigos únicos'           : [len(todos_codigos_usados)],
    })

    # ── guardar xlsx ──────────────────────────────────────────────────────────
    nombre_xlsx = nombre_archivo.replace('.csv', '.xlsx')
    with pd.ExcelWriter(nombre_xlsx, engine='openpyxl') as writer:
        resumen_agrupado .to_excel(writer, sheet_name='Resumen',          index=False)
        codigos_por_año  .to_excel(writer, sheet_name='Códigos por Año',  index=False)
        detalle_completo .to_excel(writer, sheet_name='Detalle x Código', index=False)
        lista_global     .to_excel(writer, sheet_name='Lista Códigos',    index=False)

    print('\nResumen:')
    print(resumen_agrupado.to_string())
    return resumen_agrupado
#------------------------------------------------------------------------------------------------------------------------------------------------
def descargar_comtrade(frames=None, ruta=None):
    """
    Sin argumentos → devuelve la lista de pares (para que descargar_todo los use).
    Con frames     → post-procesa y guarda los xlsx.
    """
    pares = [
        {'reporter': '842', 'partner': '0',   'flow': 'X', 'label': 'USA_W'},
        {'reporter': '842', 'partner': '0',   'flow': 'M', 'label': 'W_USA'},
        {'reporter': '484', 'partner': '0',   'flow': 'X', 'label': 'MX_W'},
        {'reporter': '484', 'partner': '0',   'flow': 'M', 'label': 'W_MX'},
        {'reporter': '124', 'partner': '0',   'flow': 'X', 'label': 'CA_W'},
        {'reporter': '124', 'partner': '0',   'flow': 'M', 'label': 'W_CA'},
        {'reporter': '484', 'partner': '124', 'flow': 'X', 'label': 'MX_CA'},
        {'reporter': '484', 'partner': '124', 'flow': 'M', 'label': 'CA_MX'},
    ]

    # ── modo consulta: solo devuelve pares ────────────────────────────────────
    if frames is None:
        return pares

    # ── modo post-proceso: guarda xlsx ────────────────────────────────────────
    if not frames:
        print('❌ Sin datos Comtrade')
        return {}

    final = pd.concat(frames.values(), ignore_index=True)
    final['netWgt_ton'] = final['netWgt'] / 1000
    final.rename(columns={
        'period':       'Año',
        'primaryValue': 'Valor Total (USD)',
        'netWgt_ton':   'Cantidad Total (toneladas)'
    }, inplace=True)

    resultados = {}
    for label, grupo in final.groupby('par'):
        ruta_xlsx = os.path.join(ruta, f'{label}.xlsx')

        resumen = grupo.groupby('Año').agg(
            cantidad_total=('Cantidad Total (toneladas)', 'sum'),
            valor_total   =('Valor Total (USD)',          'sum')
        ).reset_index().rename(columns={
            'cantidad_total': 'Cantidad Total (toneladas)',
            'valor_total':    'Valor Total (USD)'
        })

        detalle = grupo.groupby(['Año', 'cmdCode', 'cmdDesc']).agg(
            cantidad=('Cantidad Total (toneladas)', 'sum'),
            valor   =('Valor Total (USD)',          'sum')
        ).reset_index().rename(columns={
            'cmdCode':  'Código HS',
            'cmdDesc':  'Descripción',
            'cantidad': 'Cantidad (toneladas)',
            'valor':    'Valor (USD)'
        })

        with pd.ExcelWriter(ruta_xlsx, engine='openpyxl') as writer:
            resumen.to_excel(writer, sheet_name='Resumen',         index=False)
            detalle.to_excel(writer, sheet_name='Códigos por Año', index=False)

        print(f'✅ Guardado: {ruta_xlsx} — {len(grupo)} filas')
        resultados[label] = resumen

    return resultados


# ─────────────────────────────────────────────────────────────────────────────
def descargar_todo(hs_codes_gats, cmd_code_comtrade, ruta_gats, ruta_comtrade):

    # ── pregunta modo una sola vez ────────────────────────────────────────────
    print('\n¿Cómo quieres manejar múltiples códigos HS por año?')
    print('  1 → Sumar todos los códigos')
    print('  2 → Usar solo el código dominante (mayor cantidad)')
    opcion = input('Elige (1/2): ').strip()
    modo   = 'suma' if opcion == '1' else 'dominante'
    print(f'✅ Modo seleccionado: {modo}\n')
    """
    Descarga todos los flujos comerciales USMCA usando GATS y Comtrade
    en paralelo dentro de un único ThreadPoolExecutor.

    Parámetros:
    -----------
    hs_codes_gats      : list — códigos HS10 para GATS (ej. ['1214900010'])
    cmd_code_comtrade  : str  — código HS para Comtrade (ej. '121410')
    ruta_gats          : str  — carpeta RAW_DATA para GATS
    ruta_comtrade      : str  — carpeta Comercio para Comtrade
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    API_KEYS = [
        'XbgcpZL0C1IAOE71twGoL1s3goxu3al3JfDl4qgx',
        'F5ZzJgTIeCsY575htYHiQXY3pXXLhXhPk49avDZ8',
        'vWkP48GW9JkqBIHAF7WgdzK9J2y9BdyRrgrgJC7Y',
        'zjRcMyQiaAfpFBsaaVqaZULkxnm9BaDtnw2hfH2P',
        'jLOfTULWelr78pb1AV6pOsUQBLaFr6Bizk1M2V2V',
        'Cxhngox4s6tUS6Dd12myA7sviT8hOzqpmqe28cjV',
        'bMrBSyWcOdnrhl7fWWh3GAkhH9nwXf63j8bSpePF',
        'LQuOmvna7MjSbwDj2GbjydK3goq1AYxKbPnrWrrn',
        'rIUrhVfaBLCnasXMumLeObthUgwSnkZwXtSaACvD',
        'ZVOpLkwGwU8XMXqyxQwwMNv8Ikg9m96ae6gNtrtf',
        'QDeawdPuGxoBCPXlZaDffXqMKI9Kllf0g6dqqQCc'
    ]

    keys_bloqueadas = set()
    lock_keys       = threading.Lock()
    lock_print      = threading.Lock()


    # normalizar hs_codes con zfill por si vienen sin cero
    # no tocar códigos cortos, solo rellenar los de 10 dígitos
    hs_codes_norm = [
        str(c).zfill(10) if len(str(c)) == 10 else str(c)
        for c in hs_codes_gats
    ]


    # ── pares GATS ────────────────────────────────────────────────────────────
    flujos = [
        ('MX', 'X', 'USA_MX.csv'),
        ('MX', 'M', 'MX_USA.csv'),
        ('CA', 'X', 'USA_CA.csv'),
        ('CA', 'M', 'CA_USA.csv'),
    ]

    # reparte keys entre flujos GATS
    flujos_con_key = []
    n_keys, n_flujos = len(API_KEYS), len(flujos)
    base, sobrantes, idx = n_keys // n_flujos, n_keys % n_flujos, 0
    for i, (partner, tipo, archivo) in enumerate(flujos):
        cantidad = base + (1 if i < sobrantes else 0)
        keys_asignadas = [API_KEYS[(idx + j) % n_keys] for j in range(cantidad)]
        idx += cantidad
        flujos_con_key.append((partner, tipo, archivo, keys_asignadas))

    # ── tarea GATS ────────────────────────────────────────────────────────────
    def tarea_gats(args):
        partner, tipo, archivo, keys = args

        with lock_keys:
            keys_disp = [k for k in keys if k not in keys_bloqueadas]
        if not keys_disp:
            with lock_keys:
                keys_disp = [k for k in API_KEYS if k not in keys_bloqueadas]
            if not keys_disp:
                with lock_print:
                    print(f'❌ [GATS] {archivo}: todas las keys bloqueadas')
                return ('gats', archivo, pd.DataFrame())
            with lock_print:
                print(f'⚠️ [GATS] {archivo}: usando keys del pool global')

        with lock_print:
            print(f'[GATS] Iniciando {archivo}...')
        try:
            df = descargar_comercio_gats(
                hs_codes=hs_codes_norm,          # ← usar lista normalizada
                partner_code=partner,
                ruta=ruta_gats,
                nombre_archivo=archivo,
                api_keys=keys_disp,
                tipo=tipo,
                modo=modo,
                keys_bloqueadas=keys_bloqueadas,
                lock_keys=lock_keys,
                pool_global_keys=API_KEYS,
            )
            with lock_print:
                print(f'✅ [GATS] {archivo} completado')
            return ('gats', archivo, df)
        except Exception as e:
            with lock_print:
                print(f'❌ [GATS] {archivo}: {e}')
            return ('gats', archivo, pd.DataFrame())

    COMTRADE_KEYS = [
    '3c6773b3ef244d679a65cbf0a8a547f0',
    'e3eef60e51274a1cb6ae754559e5e8e5',
    '1bef6037550140e5bed7ed1b618d9639',
    '5ccb331ab6c0465aa72f2f0e9dbb0f0f',
    '08d23902550d477bba568d8249c3fb1e',
    '88fe10db87284d0ab461204bc8b3ce10', 
    '3cefc5d5c02b41beb6c9ba93a0e8b96f',
    '1457e50cafaf492d94ab3770e61408c6', 
    '10913d9d6a92456987f16eb48c4c1703',
    'ef93999ffde54f3d8ab5c03baf23ac0b',
    ]
    # Pool compartido de keys con un lock


    key_pool = deque(COMTRADE_KEYS)
    lock_keys = threading.Lock()
    
    def obtener_key():
        with lock_keys:
            if key_pool:
                return key_pool[0]
            return None
    
    def descartar_key(key):
        with lock_keys:
            try:
                key_pool.remove(key)
            except ValueError:
                pass
        
    def tarea_comtrade_par(par):
            dfs = []
            key = par['key']
            with lock_print:
                print(f'[Comtrade] Iniciando {par["label"]} con key {key[:8]}...')
    
            codigos = [c.strip() for c in cmd_code_comtrade.split(',')]
    
            for year in range(1994, 2026):
                dfs_year = []
    
                for codigo in codigos:
                    exito = False
    
                    for intento in range(3):
                        try:
                            old_stdout = sys.stdout
                            sys.stdout = buffer = io.StringIO()
                            df = comtradeapicall.getFinalData(
                                subscription_key=key,
                                typeCode='C', freqCode='A', clCode='HS',
                                period=str(year),
                                reporterCode=par['reporter'],
                                cmdCode=codigo,
                                flowCode=par['flow'],
                                partnerCode=par['partner'],
                                partner2Code=None, customsCode=None, motCode=None,
                                maxRecords=500, format_output='JSON',
                                aggregateBy=None, breakdownMode='classic',
                                countOnly=None, includeDesc=True
                            )
                            sys.stdout = old_stdout
                            captured = buffer.getvalue()
    
                            if '429' in captured or 'rate limit' in captured.lower():
                                with lock_print:
                                    print(f'  ⏳ [Comtrade] Rate limit {par["label"]} {year} '
                                          f'cod={codigo} intento {intento+1}/3, esperando 12s...')
                                time.sleep(120)
                                continue
    
                            if '403' in captured or 'quota' in captured.lower():
                                with lock_print:
                                    print(f'  🔑 [Comtrade] {par["label"]}: key {key[:8]} sin cuota, rotando...')
                                descartar_key(key)
                                key = obtener_key()
                                if key is None:
                                    with lock_print:
                                        print(f'  💀 [Comtrade] {par["label"]}: sin keys disponibles, abortando')
                                    return ('comtrade', par['label'],
                                            pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame())
                                continue  # reintentar mismo código/año con nueva key
    
                            if df is not None and not df.empty:
                                df['par'] = par['label']
                                dfs_year.append(df)
    
                            exito = True
                            break  # ok → siguiente código
    
                        except Exception as e:
                            sys.stdout = old_stdout
                            with lock_print:
                                print(f'  ❌ [Comtrade] {par["label"]} {year} '
                                      f'cod={codigo} intento {intento+1}/3: {e}')
                            time.sleep(3)
    
                    if not exito:
                        with lock_print:
                            print(f'  ⚠️ [Comtrade] {par["label"]} {year} '
                                  f'cod={codigo}: 3 intentos fallidos, sin datos para este código')
    
                    time.sleep(0.3)  # pausa entre códigos
    
                if dfs_year:
                    df_año = pd.concat(dfs_year, ignore_index=True)
                    dfs.append(df_año)
                    with lock_print:
                        tons = df_año['netWgt'].sum() / 1000 if 'netWgt' in df_año.columns else 0
                        print(f'  {year}: {len(codigos)} códigos → {len(df_año)} filas | {tons:,.0f} ton')
                else:
                    with lock_print:
                        print(f'  {year}: sin datos')
    
            result = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
            with lock_print:
                status = '✅' if not result.empty else '⚠️ sin datos'
                print(f'{status} [Comtrade] {par["label"]} completado')
            return ('comtrade', par['label'], result)

    # ── executor único: GATS + Comtrade en paralelo ───────────────────────────
    print('\n' + '='*55)
    print('DESCARGANDO GATS + COMTRADE EN PARALELO')
    print('='*55)

    resultados_gats  = {}
    frames_comtrade  = {}
    
    # ── GATS en su propio pool ────────────────────────────────────────────────────
    with ThreadPoolExecutor(max_workers=4) as executor_gats:
        futures_gats = {executor_gats.submit(tarea_gats, f): f for f in flujos_con_key}
        for future in as_completed(futures_gats):
            tipo, clave, resultado = future.result()
            resultados_gats[clave] = resultado
    
    # ── Comtrade: un hilo por par, cada uno con su key ───────────────────────────
    # ── antes de lanzar los futures ──────────────────────────────────────────────
    pares = descargar_comtrade()
    # asignar key dedicada a cada par
    for i, par in enumerate(pares):
       par['key'] = COMTRADE_KEYS[i % len(COMTRADE_KEYS)]
    with ThreadPoolExecutor(max_workers=len(pares)) as executor_cmt:
        futures_cmt = {
            executor_cmt.submit(tarea_comtrade_par, par): par
            for par in pares
        }
        for future in as_completed(futures_cmt):
            tipo, clave, resultado = future.result()
            if not resultado.empty:
                frames_comtrade[clave] = resultado
    
    # post-proceso Comtrade
    resultados_comtrade = descargar_comtrade(frames_comtrade, ruta_comtrade)


    print('\n🎉 Todo descargado ✅')
    return {
        'gats':     resultados_gats,
        'comtrade': resultados_comtrade,
    }
#-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

def porcentajes_temec(nombres, dfs, dfAG=None, productos=None):
    """
    Calcula dos tipos de porcentajes:
      pct     – distribución entre socios T-MEC (denominador = suma bilateral de socios)
      pct_ag  – participación en el total agropecuario (denominador = dfAG)
 
    Retorna (pct, pct_ag).
    """
    paises = ['C', 'U', 'M']
    if productos is None:
        productos_buscar = PRODUCTOS_DEFAULT
    else:
        productos_buscar = productos
    nombre_pais = {'C': 'Canadá', 'U': 'EE. UU.', 'M': 'México'}
 
    # Mapeo de código de país a sufijo de columna en dfAG
    _ag_code = {'C': 'CA', 'U': 'USA', 'M': 'MX'}
 
    # Indexar DFs bilaterales: bilateral[exp][imp][producto] = df
    bilateral = {p: {q: {} for q in paises if q != p} for p in paises}
 
    for nombre, df in zip(nombres, dfs):
        if nombre[0] == 'P':
            continue
        if len(nombre) < 4:
            continue
        exp = nombre[2]
        imp = nombre[3]
        if exp not in paises or imp not in paises:
            continue
        for producto in productos_buscar:
            if producto in nombre:
                bilateral[exp][imp][producto] = df.copy()
                break
 
    # Total agrario por par bilateral (suma de productos seleccionados)
    for exp in paises:
        for imp in paises:
            if imp == exp:
                continue
            prod_dfs = list(bilateral[exp][imp].values())
            if prod_dfs:
                bilateral[exp][imp]['Total'] = concat_sumar(prod_dfs)
 
    todos_productos = productos_buscar + ['Total']
    col = 'Cantidad Total (toneladas)'
 
    # ── pct: distribución entre socios T-MEC (cálculo original) ───────────────
    pct = {}
 
    for producto in todos_productos:
        pct[producto] = {}
        for pais in paises:
            socios = [p for p in paises if p != pais]
 
            # --- Exportaciones: pais exporta a socio1 y socio2 ---
            exp_dfs = {}
            for socio in socios:
                if producto in bilateral[pais].get(socio, {}):
                    exp_dfs[socio] = bilateral[pais][socio][producto].set_index('Año')
 
            if len(exp_dfs) == 2:
                años = exp_dfs[socios[0]].index.intersection(exp_dfs[socios[1]].index)
                total = sum(df.loc[años, [col]] for df in exp_dfs.values())
                for socio in socios:
                    vals = exp_dfs[socio].loc[años, [col]]
                    pct_calc = (vals / total * 100).round(2)
                    pct_calc = pct_calc.replace([float('inf'), float('-inf')], None)
                    key = f'{nombre_pais[pais]} exporta a {nombre_pais[socio]}'
                    pct[producto][key] = pct_calc
            elif len(exp_dfs) == 1:
                # Solo exporta a un socio → 100% para ese, 0% para el otro
                socio_con = list(exp_dfs.keys())[0]
                socio_sin = [s for s in socios if s != socio_con][0]
                df_con = exp_dfs[socio_con]
                años = df_con.index
                pct_100 = pd.DataFrame({col: [100.0]*len(años)}, index=años)
                pct_0   = pd.DataFrame({col: [0.0]*len(años)}, index=años)
                pct[producto][f'{nombre_pais[pais]} exporta a {nombre_pais[socio_con]}'] = pct_100
                pct[producto][f'{nombre_pais[pais]} exporta a {nombre_pais[socio_sin]}'] = pct_0
 
            # --- Importaciones: pais importa de socio1 y socio2 ---
            imp_dfs = {}
            for socio in socios:
                if producto in bilateral.get(socio, {}).get(pais, {}):
                    imp_dfs[socio] = bilateral[socio][pais][producto].set_index('Año')
 
            if len(imp_dfs) == 2:
                años = imp_dfs[socios[0]].index.intersection(imp_dfs[socios[1]].index)
                total = sum(df.loc[años, [col]] for df in imp_dfs.values())
                for socio in socios:
                    vals = imp_dfs[socio].loc[años, [col]]
                    pct_calc = (vals / total * 100).round(2)
                    pct_calc = pct_calc.replace([float('inf'), float('-inf')], None)
                    key = f'{nombre_pais[pais]} importa de {nombre_pais[socio]}'
                    pct[producto][key] = pct_calc
            elif len(imp_dfs) == 1:
                socio_con = list(imp_dfs.keys())[0]
                socio_sin = [s for s in socios if s != socio_con][0]
                df_con = imp_dfs[socio_con]
                años = df_con.index
                pct_100 = pd.DataFrame({col: [100.0]*len(años)}, index=años)
                pct_0   = pd.DataFrame({col: [0.0]*len(años)}, index=años)
                pct[producto][f'{nombre_pais[pais]} importa de {nombre_pais[socio_con]}'] = pct_100
                pct[producto][f'{nombre_pais[pais]} importa de {nombre_pais[socio_sin]}'] = pct_0
 
    # ── pct_ag: % respecto al total agropecuario (NUEVO, usa dfAG) ────────────
    pct_ag = {}
 
    if dfAG is not None and not dfAG.empty:
        ag = dfAG.set_index('Año')
 
        for producto in todos_productos:
            pct_ag[producto] = {}
            for exp in paises:
                for imp in paises:
                    if imp == exp:
                        continue
                    if producto not in bilateral[exp].get(imp, {}):
                        continue
 
                    prod_df = bilateral[exp][imp][producto].copy()
                    if 'Año' in prod_df.columns:
                        prod_df = prod_df.set_index('Año')
 
                    if col not in prod_df.columns:
                        continue
 
                    # Denominador: exportaciones totales agropecuarias del país exportador
                    col_ag = f'Export_{_ag_code[exp]}'
                    if col_ag not in ag.columns:
                        continue
 
                    años = prod_df.index.intersection(ag.index)
                    if años.empty:
                        continue
 
                    numerador   = prod_df.loc[años, col]
                    denominador = ag.loc[años, col_ag]
 
                    pct_calc = (numerador / denominador * 100).round(2)
                    pct_calc = pct_calc.replace([float('inf'), float('-inf')], None)
 
                    key_exp = f'{nombre_pais[exp]}→{nombre_pais[imp]}'
                    pct_ag[producto][key_exp] = pd.DataFrame(
                        {col: pct_calc.values}, index=pct_calc.index
                    )
 
                # Importaciones: lo que el país importa de cada socio
                # respecto al total de importaciones agropecuarias del país
                for imp_from in paises:
                    if imp_from == exp:
                        continue
                    # Aquí calculamos para el lado importador también
                    # exp es quien importa, imp_from es de dónde viene
                    pass  # ya cubierto en el loop de arriba como destino
 
            # Agregar cálculo por el lado importador
            for imp_pais in paises:
                for exp_origen in paises:
                    if exp_origen == imp_pais:
                        continue
                    if producto not in bilateral[exp_origen].get(imp_pais, {}):
                        continue
 
                    prod_df = bilateral[exp_origen][imp_pais][producto].copy()
                    if 'Año' in prod_df.columns:
                        prod_df = prod_df.set_index('Año')
 
                    if col not in prod_df.columns:
                        continue
 
                    # Denominador: importaciones totales agropecuarias del país importador
                    col_ag_imp = f'Import_{_ag_code[imp_pais]}'
                    if col_ag_imp not in ag.columns:
                        continue
 
                    años = prod_df.index.intersection(ag.index)
                    if años.empty:
                        continue
 
                    numerador   = prod_df.loc[años, col]
                    denominador = ag.loc[años, col_ag_imp]
 
                    pct_calc = (numerador / denominador * 100).round(2)
                    pct_calc = pct_calc.replace([float('inf'), float('-inf')], None)
 
                    key_imp = f'{nombre_pais[imp_pais]}←{nombre_pais[exp_origen]}'
                    # Solo agregar si no existe ya (evitar duplicados)
                    if key_imp not in pct_ag[producto]:
                        pct_ag[producto][key_imp] = pd.DataFrame(
                            {col: pct_calc.values}, index=pct_calc.index
                        )
 
    return pct, pct_ag
 
#-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
def calcular_lafay(dfso, key_x, key_m, key_p, col_ton, col_ipc):
    """Calcula Lafay dado un diccionario de dfs y las keys de X, M, Pd."""
    
    # --- Toneladas ---
    df_x = dfso[key_x][['Año', col_ton]].rename(columns={col_ton: 'X_ton'})
    df_m = dfso[key_m][['Año', col_ton]].rename(columns={col_ton: 'M_ton'})
    df_p = dfso[key_p][['Año', col_ton]].rename(columns={col_ton: 'Pd_ton'})

    # --- Valor real IPC ---
    cols_x_ipc = ['Año', col_ipc] if col_ipc in dfso[key_x].columns else ['Año']
    cols_m_ipc = ['Año', col_ipc] if col_ipc in dfso[key_m].columns else ['Año']
    cols_p_ipc = ['Año', col_ipc] if col_ipc in dfso[key_p].columns else ['Año']

    df_x_ipc = dfso[key_x][cols_x_ipc].rename(columns={col_ipc: 'X_ipc'})
    df_m_ipc = dfso[key_m][cols_m_ipc].rename(columns={col_ipc: 'M_ipc'})
    df_p_ipc = dfso[key_p][cols_p_ipc].rename(columns={col_ipc: 'Pd_ipc'})

    df = df_p.merge(df_x, on='Año', how='left') \
             .merge(df_m, on='Año', how='left') \
             .merge(df_x_ipc, on='Año', how='left') \
             .merge(df_m_ipc, on='Año', how='left') \
             .merge(df_p_ipc, on='Año', how='left') \
             .fillna(0)

    df['consumo_aparente_ton'] = df['Pd_ton'] + df['M_ton'] - df['X_ton']
    df['Lafay_ton'] = df.apply(
        lambda r: r['Pd_ton'] / r['consumo_aparente_ton'] if r['consumo_aparente_ton'] > 0 else None,
        axis=1
    )

    if 'Pd_ipc' in df.columns and 'X_ipc' in df.columns and 'M_ipc' in df.columns:
        df['consumo_aparente_ipc'] = df['Pd_ipc'] + df['M_ipc'] - df['X_ipc']
        df['Lafay_ipc'] = df.apply(
            lambda r: r['Pd_ipc'] / r['consumo_aparente_ipc'] if r['consumo_aparente_ipc'] > 0 else None,
            axis=1
        )

    cols_resultado = ['Año', 'Pd_ton', 'X_ton', 'M_ton', 'consumo_aparente_ton', 'Lafay_ton']
    if 'Lafay_ipc' in df.columns:
        cols_resultado += ['Pd_ipc', 'X_ipc', 'M_ipc', 'consumo_aparente_ipc', 'Lafay_ipc']

    return df[cols_resultado].set_index('Año')
def lafay_anual(dicDfs, productos=None):
    paises   = {'C': 'Canadá', 'U': 'USA', 'M': 'México'}
    if productos is None:
        productos = PRODUCTOS_DEFAULT
    col_ton  = 'Cantidad Total (toneladas)'
    col_ipc  = 'valor_real_ipc en miles'

    resultado = sumarDfs(list(dicDfs.keys()), list(dicDfs.values()))
    lafay     = {p: {c: None for c in paises} for p in productos + ['T']}

    for pais_cod, pais_nombre in paises.items():
        for producto in productos:
            key_x = f'df{pais_cod}W{producto}'
            key_m = f'dfW{pais_cod}{producto}'
            key_p = f'P{pais_cod}{producto}'

            if key_x not in dicDfs or key_m not in dicDfs or key_p not in dicDfs:
                print(f'Lafay ⚠️ {pais_nombre} - {producto}: faltan datos')
                continue

            lafay[producto][pais_cod] = calcular_lafay(
                dicDfs, key_x, key_m, key_p, col_ton, col_ipc
            )

        else:
            key_x = f'dfEWT_{pais_cod}'
            key_m = f'dfMWT_{pais_cod}'
            key_p = f'dfPT_{pais_cod}'

            if key_x not in resultado or key_m not in resultado or key_p not in resultado:
                print(f'Lafay ⚠️ {pais_nombre} - Total: faltan datos')
                continue

            lafay['T'][pais_cod] = calcular_lafay(
                resultado, key_x, key_m, key_p, col_ton, col_ipc
            )

    return lafay
#==================================================================================================================================================================================================================
def krugman(dicDfs, dfAG, productos=None):
    from itertools import permutations
    if productos is None:
        productos = PRODUCTOS_DEFAULT
    paises    = ['C', 'M', 'U']
    pais_col  = {'C': 'Export_CA', 'U': 'Export_USA', 'M': 'Export_MX'}
    col_ipc   = 'valor_real_ipc en miles'

    ag       = dfAG.set_index('Año')
    resultado = sumarDfs(list(dicDfs.keys()), list(dicDfs.values()), productos=productos)

    def calcular_ik(df_i, df_j, total_i, total_j):
        años = df_i.index.intersection(df_j.index).intersection(total_i.index).intersection(total_j.index)
        if años.empty:
            return None
        part_i = df_i.loc[años].squeeze() / total_i.loc[años]
        part_j = df_j.loc[años].squeeze() / total_j.loc[años]
        return (part_i - part_j).abs().rename('IK')

    pares   = list(permutations(paises, 2))
    krugman = {p: {} for p in productos + ['T']}

    for pais_i, pais_j in pares:
        total_i = ag[pais_col[pais_i]]
        total_j = ag[pais_col[pais_j]]

        for producto in productos:
            key_i = f'df{pais_i}W{producto}'
            key_j = f'df{pais_j}W{producto}'

            if key_i not in dicDfs or key_j not in dicDfs:
                print(f'Krugman ⚠️ {pais_i}-{pais_j} {producto}: falta {key_i} o {key_j}')
                continue

            df_i = dicDfs[key_i].set_index('Año')
            df_j = dicDfs[key_j].set_index('Año')

            if col_ipc not in df_i.columns or col_ipc not in df_j.columns:
                print(f'Krugman ⚠️ {pais_i}-{pais_j} {producto}: falta columna IPC')
                continue

            ik = calcular_ik(df_i[[col_ipc]], df_j[[col_ipc]], total_i, total_j)
            if ik is not None:
                krugman[producto][(pais_i, pais_j)] = ik

        else:
            # Total — usa sumarDfs
            key_i = f'dfEWT_{pais_i}'
            key_j = f'dfEWT_{pais_j}'

            if key_i not in resultado or key_j not in resultado:
                print(f'Krugman ⚠️ {pais_i}-{pais_j} Total: falta {key_i} o {key_j}')
                continue

            df_i = resultado[key_i].set_index('Año')
            df_j = resultado[key_j].set_index('Año')

            if col_ipc not in df_i.columns or col_ipc not in df_j.columns:
                print(f'Krugman ⚠️ {pais_i}-{pais_j} Total: falta columna IPC')
                continue

            ik = calcular_ik(df_i[[col_ipc]], df_j[[col_ipc]], total_i, total_j)
            if ik is not None:
                krugman['T'][(pais_i, pais_j)] = ik

    return krugman

#=======================================================================================================================================================================================================================
def concentracion(dicDfs, dfAG, productos=None):
    """
    Calcula el indicador de concentración comercial en valores reales (IPC, miles).
    X_p/X o M_p/M — participación de cada producto en el total.
    """
    if productos is None:
        productos = PRODUCTOS_DEFAULT
    paises     = ['C', 'U', 'M']
    pais_col_x = {'C': 'Export_CA', 'U': 'Export_USA', 'M': 'Export_MX'}
    pais_col_m = {'C': 'Import_CA', 'U': 'Import_USA', 'M': 'Import_MX'}
    col_ipc    = 'valor_real_ipc en miles'

    sumas   = sumarDfs(list(dicDfs.keys()), list(dicDfs.values()), productos=productos)
    ag      = dfAG.set_index('Año')
    resultado = {}

    for pais in paises:
        resultado[pais] = {'X': {}, 'M': {}}

        total_x = ag[pais_col_x[pais]]
        total_m = ag[pais_col_m[pais]]

        for producto in productos:
            key_x = f'df{pais}W{producto}'
            key_m = f'dfW{pais}{producto}'

            # Exportaciones
            if key_x in dicDfs:
                df = dicDfs[key_x].set_index('Año')
                if col_ipc in df.columns:
                    años_x = df.index.intersection(total_x.index)
                    conc_ipc = df.loc[años_x, col_ipc] / total_x.loc[años_x]
                    conc_ipc.name = 'Conc_X_ipc'
                    resultado[pais]['X'][producto] = pd.DataFrame(conc_ipc)
                else:
                    print(f'Concentracion ⚠️ {key_x}: falta columna "{col_ipc}"')
            else:
                print(f'Concentracion ⚠️ {pais} - {producto}: falta {key_x}')

            # Importaciones
            if key_m in dicDfs:
                df = dicDfs[key_m].set_index('Año')
                if col_ipc in df.columns:
                    años_m = df.index.intersection(total_m.index)
                    conc_ipc = df.loc[años_m, col_ipc] / total_m.loc[años_m]
                    conc_ipc.name = 'Conc_M_ipc'
                    resultado[pais]['M'][producto] = pd.DataFrame(conc_ipc)
                else:
                    print(f'Concentracion ⚠️ {key_m}: falta columna "{col_ipc}"')
            else:
                print(f'Concentracion ⚠️ {pais} - {producto}: falta {key_m}')

        else:
            # Total — usa sumarDfs
            key_x = f'dfEWT_{pais}'
            key_m = f'dfMWT_{pais}'

            # Exportaciones totales
            if key_x in sumas:
                df = sumas[key_x].set_index('Año')
                if col_ipc in df.columns:
                    años_x = df.index.intersection(total_x.index)
                    conc_ipc = df.loc[años_x, col_ipc] / total_x.loc[años_x]
                    conc_ipc.name = 'Conc_X_ipc'
                    resultado[pais]['X']['T'] = pd.DataFrame(conc_ipc)
                else:
                    print(f'Concentracion ⚠️ {key_x}: falta columna "{col_ipc}"')
            else:
                print(f'Concentracion ⚠️ {pais} - Total: falta {key_x}')

            # Importaciones totales
            if key_m in sumas:
                df = sumas[key_m].set_index('Año')
                if col_ipc in df.columns:
                    años_m = df.index.intersection(total_m.index)
                    conc_ipc = df.loc[años_m, col_ipc] / total_m.loc[años_m]
                    conc_ipc.name = 'Conc_M_ipc'
                    resultado[pais]['M']['T'] = pd.DataFrame(conc_ipc)
                else:
                    print(f'Concentracion ⚠️ {key_m}: falta columna "{col_ipc}"')
            else:
                print(f'Concentracion ⚠️ {pais} - Total: falta {key_m}')

    return resultado
#=======================================================================================================================================================================================================================
def vcrn(dicDfs, resultado_sumar, dfAG, productos=None, sufijos=None):
    """
    Calcula el índice VCRN para cada par exportador-importador y producto.
    ΔE_jm = E_jm/E - (E_m/E)(E_j/E)

    Parámetros:
    -----------
    dicDfs          : dict      — diccionario con DataFrames originales
    resultado_sumar : dict      — retorno de sumarDfs
    dfAG            : DataFrame — agregado mundial
    productos       : list      — lista de productos (default: PRODUCTOS_DEFAULT)
    sufijos         : dict      — sufijos por producto (default: SUFIJOS_DEFAULT)
    """
    if productos is None:
        productos = PRODUCTOS_DEFAULT
    if sufijos is None:
        sufijos = _sufijos_auto(productos)
    paises    = ['C', 'U', 'M']
    col_ton   = 'Cantidad Total (toneladas)'
    col_ipc   = 'valor_real_ipc en miles'

    resultados = {}

    for exp in paises:
        for imp in paises:
            if exp == imp:
                continue

            par = f'{exp}_{imp}'
            resultados[par] = {'ton': {}, 'ipc': {}}

            # E_m: exportaciones totales del país exp hacia imp (Total bilateral)
            key_em = f'dfET_{exp}'
            if key_em not in resultado_sumar:
                print(f'⚠️ {par}: falta {key_em}')
                continue
            df_em = resultado_sumar[key_em].set_index('Año')

            # E: oferta total en el mercado del importador
            # E = P_imp_Total - EW_imp_Total + MW_imp_Total
            key_p_imp  = f'dfPT_{imp}'
            key_ew_imp = f'dfEWT_{imp}'
            key_mw_imp = f'dfMWT_{imp}'

            if any(k not in resultado_sumar for k in [key_p_imp, key_ew_imp, key_mw_imp]):
                print(f'⚠️ {par}: faltan datos de oferta total en {imp}')
                continue

            df_p_imp  = resultado_sumar[key_p_imp].set_index('Año')
            df_ew_imp = resultado_sumar[key_ew_imp].set_index('Año')
            df_mw_imp = resultado_sumar[key_mw_imp].set_index('Año')

            for producto in productos:
                suf = sufijos[producto]

                # E_jm: exportaciones del producto j del país exp al país imp
                key_ejm = f'df{exp}{imp}{producto}'
                if key_ejm not in dicDfs:
                    print(f'⚠️ {par} - {producto}: falta {key_ejm}')
                    continue

                # E_j: oferta total del producto j en el mercado del importador
                key_p_j  = f'dfP{suf}_{imp}'
                key_ew_j = f'dfEW{suf}_{imp}'
                key_mw_j = f'dfMW{suf}_{imp}'

                if any(k not in resultado_sumar for k in [key_p_j, key_ew_j, key_mw_j]):
                    print(f'⚠️ {par} - {producto}: faltan datos de oferta de {producto} en {imp}')
                    continue

                df_ejm = dicDfs[key_ejm].set_index('Año')
                df_p_j  = resultado_sumar[key_p_j].set_index('Año')
                df_ew_j = resultado_sumar[key_ew_j].set_index('Año')
                df_mw_j = resultado_sumar[key_mw_j].set_index('Año')

                años = df_ejm.index\
                    .intersection(df_em.index)\
                    .intersection(df_p_imp.index)\
                    .intersection(df_p_j.index)

                if len(años) == 0:
                    print(f'⚠️ {par} - {producto}: sin años en común')
                    continue

                for tipo, col in [('ton', col_ton), ('ipc', col_ipc)]:
                    if tipo == 'ipc' and col not in df_ejm.columns:
                        continue

                    ejm = df_ejm.loc[años, col if tipo == 'ipc' else col_ton]
                    E_m = df_em.loc[años, col_ton]
                    E_j = df_p_j.loc[años, col_ton] - df_ew_j.loc[años, col_ton] + df_mw_j.loc[años, col_ton]
                    E   = df_p_imp.loc[años, col_ton] - df_ew_imp.loc[años, col_ton] + df_mw_imp.loc[años, col_ton]

                    vcrn_j = ejm / E - (E_m / E) * (E_j / E)
                    vcrn_j.name = f'VCRN_{producto}'
                    resultados[par][tipo][producto] = pd.DataFrame(vcrn_j)
                    print(f'✅ VCRN {par} - {producto} ({tipo}) calculado')

    return resultados

#-----------------------------------------------------------------------------------------------------------------------------------------------------

def cargar_dicDfs(ruta_producto, producto):
    paises = ['C', 'U', 'M']
    nombre_archivo = {'C': 'CA', 'U': 'USA', 'M': 'MX', 'W': 'W'}

    ruta_comercio   = os.path.join(ruta_producto, 'Comercio')
    ruta_produccion = os.path.join(ruta_producto, 'Produccion')

    dicDfs  = {}
    dfs     = []
    nombres = []

    for origen in paises + ['W']:
        for destino in paises + ['W']:
            if origen == destino:
                continue
            nombre = f'{nombre_archivo[origen]}_{nombre_archivo[destino]}.xlsx'
            ruta   = os.path.join(ruta_comercio, nombre)
            key    = f'df{origen}{destino}{producto}'
            if os.path.exists(ruta):
                try:
                    df = pd.read_excel(ruta)
                    dicDfs[key] = df
                    dfs.append(df)
                    nombres.append(key)
                    print(f'✅ {key} ← {nombre}')
                except Exception as e:
                    print(f'⚠️  {key}: {e}')
            else:
                print(f'⬛ No existe: {nombre}')

    patrones_prod = {
        'C': [f'{producto}_CA_Tons.csv', f'{producto}_CA.csv', f'{producto}_Pro_CA.csv', f'{producto}_pro_CA.csv'],
        'U': [f'{producto}_Pro_USA.csv', f'{producto}_pro_USA.csv', f'{producto}_USA.csv'],
        'M': [f'{producto}_pro_MX.csv',  f'{producto}_Pro_MX.csv',  f'{producto}_MX.csv'],
    }
    for pais, candidatos in patrones_prod.items():
        key = f'P{pais}{producto}'
        for nombre in candidatos:
            ruta = os.path.join(ruta_produccion, nombre)
            if os.path.exists(ruta):
                try:
                    df = pd.read_csv(ruta)
                    dicDfs[key] = df
                    dfs.append(df)
                    nombres.append(key)
                    print(f'✅ {key} ← {nombre}')
                    break
                except Exception as e:
                    print(f'⚠️  {key}: {e}')

    print(f'\n📦 {producto}: {len(dicDfs)} entradas cargadas')
    return dicDfs, dfs, nombres

#--------------------------------------------------------------------------------------------------------------------------------------------------
def balance_consumo_dependencia(dicDfs, productos=None):
    """
    Calcula por país, producto y año:
      - Balanza Comercial                : X - M
      - Consumo Aparente                 : Pd + M - X
      - Dependencia de Importaciones     : M / (Pd + M - X)

    Retorna:
      resultado['Balanza Comercial']['C']['Maiz']             = DataFrame
      resultado['Consumo Aparente']['M']['Sorgo']             = DataFrame
      resultado['Dependencia de Importaciones']['U']['T']     = DataFrame
    """
    if productos is None:
        productos = PRODUCTOS_DEFAULT
    paises      = ['C', 'U', 'M']
    col_ton     = 'Cantidad Total (toneladas)'
    col_ipc     = 'valor_real_ipc en miles'
    indicadores = ['Balanza Comercial', 'Consumo Aparente', 'Dependencia de Importaciones']

    sumas     = sumarDfs(list(dicDfs.keys()), list(dicDfs.values()), productos=productos)
    resultado = {ind: {p: {} for p in paises} for ind in indicadores}

    def _guardar_df(pais, producto, indicador, df_new):
        if producto not in resultado[indicador][pais]:
            resultado[indicador][pais][producto] = df_new
        else:
            resultado[indicador][pais][producto] = (
                resultado[indicador][pais][producto].join(df_new, how='outer')
            )

    def guardar(pais, producto, df_x, df_m, df_p=None):
        for col, sufijo in [(col_ton, '_ton'), (col_ipc, '_ipc')]:
            if col not in df_x.columns or col not in df_m.columns:
                continue

            # ── Balanza Comercial (solo necesita X y M) ───────────────────
            años_xm = df_x.index.intersection(df_m.index)
            if años_xm.empty:
                continue

            X = df_x.loc[años_xm, col]
            M = df_m.loc[años_xm, col]
            bal = pd.DataFrame({col: X - M}, index=años_xm)
            _guardar_df(pais, producto, 'Balanza Comercial', bal)

            # ── Consumo Aparente y Dependencia (necesitan producción) ─────
            if df_p is None or col not in df_p.columns:
                continue

            años = años_xm.intersection(df_p.index)
            if años.empty:
                continue

            X2  = df_x.loc[años, col]
            M2  = df_m.loc[años, col]
            Pd  = df_p.loc[años, col]

            con = pd.DataFrame({col: Pd + M2 - X2}, index=años)
            dep = pd.DataFrame({col: [
                M2.loc[a] / (Pd.loc[a] + M2.loc[a] - X2.loc[a])
                if (Pd.loc[a] + M2.loc[a] - X2.loc[a]) > 0 else None
                for a in años
            ]}, index=años)

            _guardar_df(pais, producto, 'Consumo Aparente', con)
            _guardar_df(pais, producto, 'Dependencia de Importaciones', dep)

    # ── Loop por país y producto ───────────────────────────────────────────
    for pais in paises:
        for producto in productos:
            key_x = f'df{pais}W{producto}'
            key_m = f'dfW{pais}{producto}'
            key_p = f'P{pais}{producto}'

            if key_x not in dicDfs or key_m not in dicDfs:
                print(f'⚠️ {pais} - {producto}: faltan datos de comercio')
                continue

            df_p = dicDfs[key_p].set_index('Año') if key_p in dicDfs else None
            if df_p is None:
                print(f'⚠️ {pais} - {producto}: sin producción, solo se calcula balanza')

            guardar(
                pais, producto,
                dicDfs[key_x].set_index('Año'),
                dicDfs[key_m].set_index('Año'),
                df_p,
            )

        # ── Total ─────────────────────────────────────────────────────────
        key_x = f'dfEWT_{pais}'
        key_m = f'dfMWT_{pais}'
        key_p = f'dfPT_{pais}'

        if key_x not in sumas or key_m not in sumas:
            print(f'⚠️ {pais} - Total: faltan datos de comercio')
            continue

        df_p_total = sumas[key_p].set_index('Año') if key_p in sumas else None
        if df_p_total is None:
            print(f'⚠️ {pais} - Total: sin producción, solo se calcula balanza')

        guardar(
            pais, 'T',
            sumas[key_x].set_index('Año'),
            sumas[key_m].set_index('Año'),
            df_p_total,
        )

    return resultado
#-------------------------------------------------------------------------------------------------------------------------------------------

def IGLL(nombres, dfs, productos=None, sufijos=None):
    if productos is None:
        productos = PRODUCTOS_DEFAULT
    if sufijos is None:
        sufijos = _sufijos_auto(productos)
    resultado = sumarDfs(nombres, dfs, productos=productos, sufijos=sufijos)
    nombres_df = list(resultado.keys())

    def get_df(nombre):
        return resultado.get(nombre, pd.DataFrame())

    paises = ['C', 'U', 'M']
    agregado = ['T-MEC', 'Mundiales']
    productos_cod = [sufijos[p] for p in productos] + ['T']
    
    igll = {}
    for p in productos_cod:
        igll[p] = {
            'C': {g: None for g in agregado},
            'U': {g: None for g in agregado},
            'M': {g: None for g in agregado},
        }

  
    dte = {}
    for pais in paises:
        dte[pais] = {
           'E':  {y: None for y in productos_cod},
           'M':  {y: None for y in productos_cod},
           'EW': {y: None for y in productos_cod},
           'MW': {y: None for y in productos_cod}
        }
        for suf in productos_cod:
            for flujo in ['E', 'M', 'EW', 'MW']:
                key = f'df{flujo}{suf}_{pais}'
                if key in resultado:
                    dte[pais][flujo][suf] = key
    
    
    for v in paises:
        for p in productos_cod:
            for f, ff, g in [('E', 'M', 'T-MEC'), ('EW', 'MW', 'Mundiales')]:
                E = get_df(dte[v][f][p])
                M = get_df(dte[v][ff][p])
                if E is None or M is None or E.empty or M.empty:
                    print(f'Df del pais: {v}, del producto {p} en el agregado {g} no se encunetra')
                    continue
                E = E.set_index('Año')
                M = M.set_index('Año')
                años_comunes = E.index.intersection(M.index)
                E = E.loc[años_comunes]
                M = M.loc[años_comunes]
                if E.columns.tolist() == M.columns.tolist():
                    igl_calc = 1 -((E - M).abs() / (E + M))
                    igll[p][v][g] = igl_calc.replace([float('inf'), float('-inf')], None)
                else:
                    E = E.loc[años_comunes, ['Cantidad Total (toneladas)']]
                    M = M.loc[años_comunes, ['Cantidad Total (toneladas)']]
                    igl_calc = 1 -((E - M).abs() / (E + M))
                    igll[p][v][g] = igl_calc.replace([float('inf'), float('-inf')], None)
    return igll
#----------------------------------------------------------------------------------------------------------------------------------------------------------


PALETTE  = ['#2196F3', '#F44336', '#4CAF50', '#FF9800', '#9C27B0', '#00BCD4']
MARKERS  = ['o', 's', '^', 'D', 'v', 'P']
LW       = 2.5
MS       = 6
FIGSIZE  = (13, 6)
TITLE_FS = 15
LABEL_FS = 12
TICK_FS  = 10
LEG_FS   = 10
 
def smart_formatter(ax, values):
    vals = [v for v in values if v is not None and not pd.isna(v)]
    if not vals:
        return
    max_val = max(abs(v) for v in vals)
    if max_val >= 1e6:
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{x/1e6:.1f}M'))
    elif max_val >= 1e3:
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{x/1e3:.1f}K'))
    else:
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{x:.3f}'))
 
def estilo_linea(ax, df_g, x, y, hue, ylabel, ylim=None):
    for i, cat in enumerate(df_g[hue].unique()):
        sub = df_g[df_g[hue] == cat].sort_values(x)
        ax.plot(sub[x], sub[y],
                color=PALETTE[i % len(PALETTE)],
                marker=MARKERS[i % len(MARKERS)],
                linewidth=LW, markersize=MS, label=cat)
    ax.set_xlabel('Año', fontsize=LABEL_FS)
    ax.set_ylabel(ylabel, fontsize=LABEL_FS)
    ax.tick_params(axis='x', rotation=45, labelsize=TICK_FS)
    ax.tick_params(axis='y', labelsize=TICK_FS)
    ax.grid(True, linestyle='--', alpha=0.4)
    ax.spines[['top', 'right']].set_visible(False)
    if ylim:
        ax.set_ylim(ylim)
    smart_formatter(ax, df_g[y].dropna().tolist())
 
def agregar_leyenda(ax, titulo_leyenda):
    ax.legend(title=titulo_leyenda, fontsize=LEG_FS,
              title_fontsize=LEG_FS + 1,
              framealpha=0.9, edgecolor='#cccccc', loc='best')
 
def base_fig(titulo):
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.set_title(titulo, fontsize=TITLE_FS, fontweight='bold', pad=14)
    return fig, ax
 
def graficar_indicador(lafay, krugman, concentracion, igll, balance_cd,
                       dicDfs=None, resultado_sumar=None,
                       ruta_base=r'C:\Users\luisc\OneDrive\PIAV\Indicadores\Graficas',
                       productos=None):
    sns.set_theme(style='whitegrid', palette=PALETTE)
    paises    = ['C', 'U', 'M']
    if productos is None:
        productos = PRODUCTOS_DEFAULT
    productos = list(productos) + ['T']
 
    def guardar(fig, carpeta, nombre):
        ruta = os.path.join(ruta_base, carpeta)
        os.makedirs(ruta, exist_ok=True)
        nombre_limpio = (nombre.replace(' ', '_').replace('→', '-')
                               .replace('(', '').replace(')', '')
                               .replace('/', '-').replace(':', '')
                               .replace('[', '').replace(']', '')
                               .replace('—', '-'))
        fig.savefig(os.path.join(ruta, f'{nombre_limpio}.png'),
                    dpi=150, bbox_inches='tight')
        plt.close(fig)
 
    # ─────────────────────────────────────────────────────────────────────────
    # Helper: grafica "por país" — un gráfico por país con todos los productos
    # Igual que grafica_producto: cada línea = un producto, cada gráfico = un país
    # ─────────────────────────────────────────────────────────────────────────
    def graficar_por_pais(getter_fn, col, ylabel, titulo_fn,
                          carpeta, ylim=None, ref_line=None):
        """
        getter_fn(pais, producto) → Series o None
        titulo_fn(pais_label, col_bonita) → str
        """
        for pais in paises:
            filas = []
            for producto in productos:
                s = getter_fn(pais, producto)
                if s is None or (hasattr(s, 'empty') and s.empty):
                    continue
                df_p = s.dropna().reset_index()
                df_p.columns = ['Año', col]
                df_p['Producto'] = producto
                filas.append(df_p)
            if not filas:
                continue
            df_g = pd.concat(filas, ignore_index=True)
            col_bonita = colu_boni(col)
            titulo     = titulo_fn(get_flujo(pais), col_bonita)
            fig, ax    = base_fig(titulo)
            estilo_linea(ax, df_g, 'Año', col, 'Producto', ylabel=col_bonita, ylim=ylim)
            if ref_line is not None:
                ax.axhline(ref_line, color='grey', linestyle=':',
                           linewidth=1.2, label=f'Ref. {ref_line}')
            agregar_leyenda(ax, 'Producto')
            plt.tight_layout()
            guardar(fig, carpeta, titulo)
 
    # ── 1. LAFAY ──────────────────────────────────────────────────────────────
    print('Graficando Lafay...')
 
    # por producto (todos los países en un gráfico)
    for producto in productos:
        filas = []
        for pais in paises:
            df = lafay.get(producto, {}).get(pais)
            if df is None:
                continue
            df = df.reset_index()
            df['País'] = get_flujo(pais)
            filas.append(df)
        if not filas:
            continue
        df_g = pd.concat(filas, ignore_index=True)
        for col in ['Lafay_ton', 'Lafay_ipc']:
            if col not in df_g.columns:
                continue
            titulo = f'Índice de Lafay — {producto} ({colu_boni(col)})'
            fig, ax = base_fig(titulo)
            estilo_linea(ax, df_g, 'Año', col, 'País', ylabel=colu_boni(col))
            agregar_leyenda(ax, 'País')
            plt.tight_layout()
            guardar(fig, 'Lafay', titulo)
 
    # por país (todos los productos en un gráfico)
    for col in ['Lafay_ton', 'Lafay_ipc']:
        graficar_por_pais(
            getter_fn  = lambda pais, prod: (
                lafay.get(prod, {}).get(pais)[col].dropna()
                if lafay.get(prod, {}).get(pais) is not None
                and col in lafay.get(prod, {}).get(pais).columns else None
            ),
            col        = col,
            ylabel     = colu_boni(col),
            titulo_fn  = lambda pl, cb: f'Índice de Lafay — {pl} — Todos los productos ({cb})',
            carpeta    = 'Lafay',
        )
    print('  ✓ Lafay')
 
    # ── 2. KRUGMAN ────────────────────────────────────────────────────────────
    print('Graficando Krugman...')
 
    # por producto
    for producto_k in krugman:
        filas = []
        for par_tuple, series in krugman[producto_k].items():
            if series is None:
                continue
            df_par = series.reset_index()
            df_par.columns = ['Año', 'IK']
            df_par['Par'] = f'{get_flujo(par_tuple[0])} → {get_flujo(par_tuple[1])}'
            filas.append(df_par)
        if not filas:
            continue
        df_g = pd.concat(filas, ignore_index=True)
        titulo = f'Índice de Krugman — {producto_k} ({colu_boni("IK")})'
        fig, ax = base_fig(titulo)
        estilo_linea(ax, df_g, 'Año', 'IK', 'Par',
                     ylabel=colu_boni('IK'), ylim=(0, 2))
        agregar_leyenda(ax, 'Par de países')
        plt.tight_layout()
        guardar(fig, 'Krugman', titulo)
 
    # por país — cada línea es un par, comparando todos los productos
    for pais in paises:
        for par_tuple in [(pais, p2) for p2 in paises if p2 != pais]:
            filas = []
            for producto_k in krugman:
                s = krugman[producto_k].get(par_tuple)
                if s is None:
                    continue
                df_p = s.dropna().reset_index()
                df_p.columns = ['Año', 'IK']
                df_p['Producto'] = producto_k
                filas.append(df_p)
            if not filas:
                continue
            df_g   = pd.concat(filas, ignore_index=True)
            par_lb = f'{get_flujo(par_tuple[0])} → {get_flujo(par_tuple[1])}'
            titulo = f'Índice de Krugman — {par_lb} — Todos los productos ({colu_boni("IK")})'
            fig, ax = base_fig(titulo)
            estilo_linea(ax, df_g, 'Año', 'IK', 'Producto',
                         ylabel=colu_boni('IK'), ylim=(0, 2))
            agregar_leyenda(ax, 'Producto')
            plt.tight_layout()
            guardar(fig, 'Krugman', titulo)
    print('  ✓ Krugman')
 
    # ── 3. CONCENTRACIÓN ──────────────────────────────────────────────────────
    print('Graficando Concentración...')
 
    # por producto
    for flujo_label, flujo_key in [('Exportaciones', 'X'), ('Importaciones', 'M')]:
        for producto in productos:
            filas = []
            for pais in paises:
                df = concentracion.get(pais, {}).get(flujo_key, {}).get(producto)
                if df is None:
                    continue
                df = df.reset_index()
                df['País'] = get_flujo(pais)
                filas.append(df)
            if not filas:
                continue
            df_g = pd.concat(filas, ignore_index=True)
            cols_conc = [c for c in df_g.columns if 'Conc' in c]
            if not cols_conc:
                continue
            col    = cols_conc[0]
            titulo = f'Concentración de {flujo_label} — {producto} ({colu_boni(col)})'
            fig, ax = base_fig(titulo)
            estilo_linea(ax, df_g, 'Año', col, 'País', ylabel=colu_boni(col))
            agregar_leyenda(ax, 'País')
            plt.tight_layout()
            guardar(fig, 'Concentracion', titulo)
 
        # por país — todos los productos en un gráfico
        col_conc = f'Conc_{flujo_key}_ipc'
        graficar_por_pais(
            getter_fn = lambda pais, prod: (
                concentracion.get(pais, {}).get(flujo_key, {}).get(prod)[col_conc].dropna()
                if concentracion.get(pais, {}).get(flujo_key, {}).get(prod) is not None
                and col_conc in concentracion.get(pais, {}).get(flujo_key, {}).get(prod).columns
                else None
            ),
            col       = col_conc,
            ylabel    = colu_boni(col_conc),
            titulo_fn = lambda pl, cb: f'Concentración de {flujo_label} — {pl} — Todos los productos ({cb})',
            carpeta   = 'Concentracion',
        )
    print('  ✓ Concentración')
 
    # ── 4. GRUBEL-LLOYD ───────────────────────────────────────────────────────
    print('Graficando Grubel-Lloyd...')
    col_labels = {
        'Cantidad Total (toneladas)': 'ton',
        'valor_real_ipc en miles':    'ipc',
    }
    for producto in productos:
        for agregado in ['T-MEC', 'Mundiales']:
            filas_por_col = {}
            for pais in paises:
                df = igll.get(producto, {}).get(pais, {}).get(agregado)
                if df is None:
                    continue
                df = df.reset_index()
                for col in [c for c in df.columns if c != 'Año']:
                    sufijo = col_labels.get(col, col)
                    df_plot = df[['Año', col]].copy()
                    df_plot.columns = ['Año', 'IGL']
                    df_plot['País'] = get_flujo(pais)
                    filas_por_col.setdefault(sufijo, []).append(df_plot)
 
            for sufijo, filas in filas_por_col.items():
                if not filas:
                    continue
                df_g   = pd.concat(filas, ignore_index=True)
                titulo = (f'Índice de Grubel-Lloyd — {producto} '
                          f'({agregado}) [{colu_boni(sufijo)}]')
                fig, ax = base_fig(titulo)
                estilo_linea(ax, df_g, 'Año', 'IGL', 'País',
                             ylabel=colu_boni('IGL'), ylim=(0, 1))
                ax.axhline(0.5, color='grey', linestyle=':', linewidth=1.2, label='Ref. 0.5')
                agregar_leyenda(ax, 'País')
                plt.tight_layout()
                guardar(fig, 'Grubel_Lloyd', titulo)
 
    # por país — todos los productos en un gráfico
    for agregado in ['T-MEC', 'Mundiales']:
        for sufijo, col_orig in [('ton', 'Cantidad Total (toneladas)'),
                                  ('ipc', 'valor_real_ipc en miles')]:
            for pais in paises:
                filas = []
                for producto in productos:
                    df = igll.get(producto, {}).get(pais, {}).get(agregado)
                    if df is None:
                        continue
                    df = df.reset_index()
                    cols_igl = [c for c in df.columns if c != 'Año' and col_orig in c]
                    if not cols_igl:
                        # intentar por posición si solo hay una columna no-Año
                        cols_igl = [c for c in df.columns if c != 'Año']
                    if not cols_igl:
                        continue
                    col = cols_igl[0]
                    df_plot = df[['Año', col]].copy()
                    df_plot.columns = ['Año', 'IGL']
                    df_plot['Producto'] = producto
                    filas.append(df_plot)
                if not filas:
                    continue
                df_g   = pd.concat(filas, ignore_index=True)
                titulo = (f'Índice de Grubel-Lloyd — {get_flujo(pais)} '
                          f'({agregado}) [{colu_boni(sufijo)}] — Todos los productos')
                fig, ax = base_fig(titulo)
                estilo_linea(ax, df_g, 'Año', 'IGL', 'Producto',
                             ylabel=colu_boni('IGL'), ylim=(0, 1))
                ax.axhline(0.5, color='grey', linestyle=':', linewidth=1.2, label='Ref. 0.5')
                agregar_leyenda(ax, 'Producto')
                plt.tight_layout()
                guardar(fig, 'Grubel_Lloyd', titulo)
    print('  ✓ Grubel-Lloyd')
 
    # ── 5. CONSUMO APARENTE / DEPENDENCIA ─────────────────────────────────────
    print('Graficando Consumo y Dependencia...')
    indicadores_bd = [
        'Consumo Aparente',
        'Dependencia de las Importaciones',
    ]
    for indicador in indicadores_bd:
        # por producto
        for producto in productos:
            filas = []
            for pais in paises:
                df = balance_cd.get(indicador, {}).get(pais, {}).get(producto)
                if df is None:
                    continue
                df = df.reset_index()
                df['País'] = get_flujo(pais)
                filas.append(df)
            if not filas:
                continue
            df_g     = pd.concat(filas, ignore_index=True)
            cols_val = [c for c in df_g.columns if c not in ('Año', 'País')]
            for col in cols_val:
                titulo = f'{indicador} — {producto} ({colu_boni(col)})'
                fig, ax = base_fig(titulo)
                estilo_linea(ax, df_g, 'Año', col, 'País', ylabel=colu_boni(col))
                agregar_leyenda(ax, 'País')
                plt.tight_layout()
                guardar(fig, 'Balance_Consumo_Dependencia', titulo)
 
        # por país — todos los productos en un gráfico
        df_muestra = None
        for pais in paises:
            for prod in productos:
                df_m = balance_cd.get(indicador, {}).get(pais, {}).get(prod)
                if df_m is not None:
                    df_muestra = df_m
                    break
            if df_muestra is not None:
                break
        if df_muestra is None:
            continue
        cols_val = [c for c in df_muestra.columns if c != 'Año']
 
        for col in cols_val:
            graficar_por_pais(
                getter_fn = lambda pais, prod: (
                    balance_cd.get(indicador, {}).get(pais, {}).get(prod)[col].dropna()
                    if balance_cd.get(indicador, {}).get(pais, {}).get(prod) is not None
                    and col in balance_cd.get(indicador, {}).get(pais, {}).get(prod).columns
                    else None
                ),
                col       = col,
                ylabel    = colu_boni(col),
                titulo_fn = lambda pl, cb: f'{indicador} — {pl} — Todos los productos ({cb})',
                carpeta   = 'Balance_Consumo_Dependencia',
            )
    print('  ✓ Consumo / Dependencia')
 
    print(f'\n✅ Todas las gráficas guardadas en: {ruta_base}')
 
    # ── 6. FLUJOS TOTALES (estilo grafica_producto) ────────────────────────────
    # Replica los 5 gráficos de grafica_producto pero para el total de los 3 productos
    if dicDfs is None and resultado_sumar is None:
        print(f'\n✅ Todas las gráficas guardadas en: {ruta_base}')
        return
 
    print('Graficando flujos totales (estilo grafica_producto)...')
 
    PRODUCTOS_IND = [p for p in productos if p != 'T']
    col_ton = 'Cantidad Total (toneladas)'
    col_ipc = 'valor_real_ipc en miles'
    pares_todos = [
        ('C','U'), ('C','M'), ('C','W'),
        ('U','C'), ('U','M'), ('U','W'),
        ('M','C'), ('M','U'), ('M','W'),
        ('W','C'), ('W','U'), ('W','M'),
    ]
    pares_pais = {
        'C': [('C','U'), ('C','M'), ('C','W'), ('U','C'), ('M','C'), ('W','C')],
        'U': [('U','C'), ('U','M'), ('U','W'), ('C','U'), ('M','U'), ('W','U')],
        'M': [('M','C'), ('M','U'), ('M','W'), ('C','M'), ('U','M'), ('W','M')],
    }
 
    def sumar_productos_pair(exp, imp):
        """Suma los 3 productos para un par exp→imp desde dicDfs."""
        dfs_pair = []
        for prod in PRODUCTOS_IND:
            key = f'df{exp}{imp}{prod}'
            if key in dicDfs:
                df_p = dicDfs[key].set_index('Año')
                cols_num = [c for c in [col_ton, col_ipc] if c in df_p.columns]
                dfs_pair.append(df_p[cols_num])
        if not dfs_pair:
            return None
        df_sum = dfs_pair[0].copy()
        for df_p in dfs_pair[1:]:
            df_sum = df_sum.add(df_p, fill_value=0)
        return df_sum.reset_index()
 
    def graficar_flujos_total(df_g, col_original, titulo, leyenda_titulo, carpeta):
        if df_g.empty or col_original not in df_g.columns:
            return
        col_bonita = colu_boni(col_original)
        fig, ax = base_fig(titulo)
        estilo_linea(ax, df_g, 'Año', col_original, 'Flujo', ylabel=col_bonita)
        agregar_leyenda(ax, leyenda_titulo)
        plt.tight_layout()
        guardar(fig, 'Total_Productos', titulo)
 
    # ── Construir DataFrames de flujos bilaterales totales ─────────────────────
    df_bilateral = pd.DataFrame()
    for exp, imp in pares_todos:
        df_p = sumar_productos_pair(exp, imp)
        if df_p is None:
            continue
        df_p['Flujo'] = get_flujo(f'{exp}{imp}')
        df_bilateral = pd.concat([df_bilateral, df_p], ignore_index=True)
 
    # ── Exportaciones / importaciones totales por país (desde resultado_sumar) ──
    df_exp_total = pd.DataFrame()
    df_imp_total = pd.DataFrame()
    df_prod_total = pd.DataFrame()
 
    if resultado_sumar:
        for pais in ['C', 'U', 'M']:
            # exportaciones al mundo
            key_ew = f'dfEWT_{pais}'
            if key_ew in resultado_sumar:
                df_p = resultado_sumar[key_ew].copy()
                df_p['Flujo'] = get_flujo(f'EWT_{pais}')
                df_exp_total = pd.concat([df_exp_total, df_p], ignore_index=True)
            # importaciones del mundo
            key_mw = f'dfMWT_{pais}'
            if key_mw in resultado_sumar:
                df_p = resultado_sumar[key_mw].copy()
                df_p['Flujo'] = get_flujo(f'MWT_{pais}')
                df_imp_total = pd.concat([df_imp_total, df_p], ignore_index=True)
            # produccion
            key_p = f'dfPT_{pais}'
            if key_p in resultado_sumar:
                df_p = resultado_sumar[key_p].copy()
                df_p['Flujo'] = get_flujo(pais)
                df_prod_total = pd.concat([df_prod_total, df_p], ignore_index=True)
 
    # ── Balance comercial total ────────────────────────────────────────────────
    df_balance = pd.DataFrame()
    if resultado_sumar:
        for pais in ['C', 'U', 'M']:
            key_ew = f'dfEWT_{pais}'
            key_mw = f'dfMWT_{pais}'
            if key_ew in resultado_sumar and key_mw in resultado_sumar:
                df_e = resultado_sumar[key_ew].set_index('Año')
                df_m = resultado_sumar[key_mw].set_index('Año')
                cols_comunes = [c for c in [col_ton, col_ipc]
                                if c in df_e.columns and c in df_m.columns]
                if not cols_comunes:
                    continue
                temp = (df_e[cols_comunes] - df_m[cols_comunes]).reset_index()
                temp['Flujo'] = f'Balance de {get_flujo(pais)}'
                df_balance = pd.concat([df_balance, temp], ignore_index=True)
 
    # ── Generar los 5 tipos de gráficas por columna ───────────────────────────
    graficas_config = [
        (df_bilateral,  'Exportaciones totales de los 3 productos por flujo comercial',  'Flujo comercial'),
        (df_prod_total, 'Producción total de los 3 productos',                           'País'),
        (df_exp_total,  'Exportaciones totales al mundo por país',                       'País'),
        (df_imp_total,  'Importaciones totales del mundo por país',                      'País'),
        (df_balance,    'Balance comercial total de los 3 productos',                    'Balance'),
    ]
 
    for df_g, titulo_base, leyenda_titulo in graficas_config:
        if df_g is None or df_g.empty:
            continue
        for col in [col_ton, col_ipc]:
            if col not in df_g.columns:
                continue
            titulo = f'{titulo_base} ({colu_boni(col)}) (1995–2024)'
            graficar_flujos_total(df_g, col, titulo, leyenda_titulo, 'Total_Productos')
 
    print('  ✓ Flujos totales')
    print(f'\n✅ Todas las gráficas guardadas en: {ruta_base}')
    
#-----------------------------------------------------------------------------------------------------------------------------------------
import os, io, zipfile
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.chart import LineChart, Reference
from openpyxl.chart.series import DataPoint
from openpyxl.chart.layout import Layout, ManualLayout
from lxml import etree


def exportar_excel_por_pais(igll, lafay, conc, ik, dicDfs,
                             balance_cd=None,
                             porcentajes=None,          # ← NUEVO
                             porcentajes_ag=None,        # ← NUEVO
                             ruta_base=r'C:\Users\luisc\OneDrive\PIAV',
                             productos=None):
 
    # ── Paleta ────────────────────────────────────────────────────────────────
    DARK  = "1F4E78"
    MED   = "2E75B6"
    LIGHT = "EAF1FB"
    LINK  = "0563C1"
 
    # ── Mapas ─────────────────────────────────────────────────────────────────
    paises     = ['C', 'U', 'M']
    if productos is None:
        productos = PRODUCTOS_DEFAULT
    np_        = {'C': 'Canadá', 'U': 'EE. UU.', 'M': 'México'}
    np_com     = {'C': 'Canadá', 'U': 'EE. UU.', 'M': 'México', 'W': 'Mundo'}
    nprod      = {p: p for p in productos}
    nprod.update({'Maiz': 'Maíz', 'T': 'Total'})
    pl         = {
        ('C','U'): 'Canadá–EE.UU.', ('C','M'): 'Canadá–México',
        ('U','M'): 'EE.UU.–México', ('U','C'): 'EE.UU.–Canadá',
        ('M','C'): 'México–Canadá', ('M','U'): 'México–EE.UU.',
    }
    pares_x_p = {
        'C': [('C','U'), ('C','M')],
        'U': [('U','C'), ('U','M')],
        'M': [('M','C'), ('M','U')],
    }
 
    pares_com_x_pais = {
     'C': [('C','U'), ('C','M'), ('C','W'), ('U','C'), ('M','C'), ('W','C')],
     'U': [('U','C'), ('U','M'), ('U','W'), ('C','U'), ('M','U'), ('W','U')],
     'M': [('M','C'), ('M','U'), ('M','W'), ('C','M'), ('U','M'), ('W','M')],
    }
 
    col_ipc    = 'valor_real_ipc en miles'
    col_ton    = 'Cantidad Total (toneladas)'
 
    # ── resultado_sumar calculado internamente ────────────────────────────────
    from funciones import sumarDfs, colu_boni
    resultado_sumar = sumarDfs(list(dicDfs.keys()), list(dicDfs.values()), productos=productos)
 
    CB_TON = 'Miles de toneladas metricas'
    CB_IPC = 'Valor en miles de dólares ajustados por IPC (base 2018)'
 
    # ── Citas ─────────────────────────────────────────────────────────────────
    cita_gral = ('United States Department of Agriculture, Foreign Agricultural Service. (2026). Global Agricultural Trade System (GATS). Recuperado el 19 de julio de 2026, de https://apps.fas.usda.gov/gats/default.aspx; United Nations Statistics Division. (2026). UN Comtrade Database. Recuperado el 19 de julio de 2026, de https://comtradeplus.un.org.')
    cita_laf  = ('United States Department of Agriculture, Foreign Agricultural Service. (2026). Global Agricultural Trade System (GATS). Recuperado el 19 de julio de 2026, de https://apps.fas.usda.gov/gats/default.aspx; United Nations Statistics Division. (2026). UN Comtrade Database. Recuperado el 19 de julio de 2026, de https://comtradeplus.un.org/; Statistics Canada. (2026). Statistics Canada Data. Recuperado el 11 de julio de 2026, de https://www.statcan.gc.ca/; Secretaría de Agricultura y Desarrollo Rural. (2026). Sistema de Información Agroalimentaria de Consulta (SIACON-NG). Recuperado el 11 de julio de 2026, de https://www.gob.mx/agricultura/dgsiap/documentos/siacon-ng-161430; United States Department of Agriculture, National Agricultural Statistics Service. (2026). Quick Stats 2.0. Recuperado el 11 de julio de 2026, de https://quickstats.nass.usda.gov/')
    META = {
        'IGLL':      {'formula':   'IGLL = 1 −( |X − M| / (X + M) )',
                      'variables': 'X = exportaciones; M = importaciones.',
                      'interp':    '≈0: interindustrial; ≈1: intraindustrial.',
                      'fuente':    'https://apps.fas.usda.gov/gats/default.aspx, https://comtradeplus.un.org/',
                      'result_fn': lambda v: 'comercio intraindustrial.' if v >= 0.5 else 'comercio interindustrial.'},
 
        'Lafay':     {'formula':   'IL^k = Pd / (Pd + M − X)',
                      'variables': 'Pd = producción doméstica; M = importaciones; X = exportaciones.',
                      'interp':    '>1: exportador neto; <1: importador neto.',
                      'fuente':    'https://apps.fas.usda.gov/gats/default.aspx, https://comtradeplus.un.org/, https://www.statcan.gc.ca/, https://www.gob.mx/agricultura/dgsiap/documentos/siacon-ng-161430, https://quickstats.nass.usda.gov/',
                      'result_fn':  lambda v: 'exportador neto.' if v > 1 else 'importador neto.'},
 
        'Conc':      {'formula':   'C_p = F_p / F_total',
                       'variables': 'F_p = exportaciones o importaciones del producto p; F_total = total agrícola del país.',
                       'interp':    '≈0: baja participación; ≈1: alta participación en el comercio agrícola total.',
                       'fuente':    'https://apps.fas.usda.gov/gats/default.aspx, https://comtradeplus.un.org/',
                       'result_fn': lambda v: 'alta concentración.' if v > 0.5 else 'baja participación relativa.'},
 
        'Krugman':   {'formula':   'IK = Σ |P_ki − P_kj|',
                      'variables': 'P_ki = participación del producto k en las exportaciones totales del país i; '
                                   'P_kj = participación del producto k en las exportaciones totales del país j; k = producto',
                      'interp':    '0: estructuras idénticas; 2: totalmente distintas.',
                      'fuente':    'https://apps.fas.usda.gov/gats/default.aspx, https://comtradeplus.un.org/',
                      'result_fn': lambda v: 'estructuras similares.' if v < 1.0 else 'estructuras distintas.'},
 
        'Comercio':  {'formula':   'Flujos bilaterales entre países T-MEC y con el Mundo.',
                      'variables': 'Exportaciones e importaciones en toneladas y valor real.',
                      'interp':    'Evolución y dirección del comercio bilateral.',
                      'fuente':    'https://apps.fas.usda.gov/gats/default.aspx, https://comtradeplus.un.org/'},
 
        'Produccion':{'formula':   'Producción interna anual.',
                      'variables': 'Producción en toneladas y valor real.',
                      'interp':    'Capacidad productiva de cada país.',
                      'fuente':    'https://www.statcan.gc.ca/, https://www.gob.mx/agricultura/dgsiap/documentos/siacon-ng-161430, https://quickstats.nass.usda.gov/'},
 
        'Consumo Aparente':            {'formula':   'CA = Pd + M − X',
                                        'variables': 'Pd = producción; M = importaciones; X = exportaciones.',
                                        'interp':    'Disponibilidad interna total del producto.',
                                        'fuente':    'https://apps.fas.usda.gov/gats/default.aspx, https://comtradeplus.un.org/, https://www.statcan.gc.ca/, https://www.gob.mx/agricultura/dgsiap/documentos/siacon-ng-161430, https://quickstats.nass.usda.gov/'},
 
        'Balanza Comercial':            {'formula':   'BC = X − M',
                                        'variables': 'X = exportaciones; M = importaciones.',
                                        'interp':    'Diferencia entre exportaciones e importaciones.',
                                        'fuente':    'https://apps.fas.usda.gov/gats/default.aspx, https://comtradeplus.un.org/'},
 
        'Dependencia de Importaciones':{'formula':  'DI = M / (Pd + M − X)',
                                        'variables':'M = importaciones del producto; Pd = producción doméstica; X = exportaciones.',
                                        'interp':   '≈0: autosuficiente; ≈1: dependiente de importaciones.',
                                        'fuente':   'https://apps.fas.usda.gov/gats/default.aspx, https://comtradeplus.un.org/, https://www.statcan.gc.ca/, https://www.gob.mx/agricultura/dgsiap/documentos/siacon-ng-161430, https://quickstats.nass.usda.gov/'},
 
        # ══════════════════════════════════════════════════════════════════════
        # NUEVAS ENTRADAS META
        # ══════════════════════════════════════════════════════════════════════
        'Pct T-MEC':  {'formula':   '% = (Flujo del producto al socio / Σ flujos del producto a ambos socios T-MEC) × 100',
                       'variables': 'Flujo bilateral del producto entre dos socios T-MEC; Suma de flujos a ambos socios.',
                       'interp':    'Distribución porcentual del comercio de un producto entre socios T-MEC.',
                       'fuente':    'https://apps.fas.usda.gov/gats/default.aspx, https://comtradeplus.un.org/'},
 
        'Pct Agropecuario': {'formula':   '% = (Flujo bilateral del producto / Total agropecuario del país según Comtrade) × 100',
                             'variables': 'Flujo bilateral del producto; Total del comercio agropecuario del país (dfAG).',
                             'interp':    '≈0: participación mínima; valor alto: producto relevante en la canasta agropecuaria.',
                             'fuente':    'https://apps.fas.usda.gov/gats/default.aspx, https://comtradeplus.un.org/'},
    }
 
    # ── Helpers de formato ────────────────────────────────────────────────────
    def fill_(hex_): return PatternFill("solid", start_color=hex_, end_color=hex_)
    def fnt_(color="000000", bold=False, italic=False, sz=10):
        return Font(name='Arial', size=sz, bold=bold, italic=italic, color=color)
 
    def setup_cols(ws):
        ws.column_dimensions['A'].width = 2
        ws.column_dimensions['B'].width = 28
        for c in 'CDEF':
            ws.column_dimensions[c].width = 16
        for c in 'GHIJKLMNOPQRSTUVWX':
            ws.column_dimensions[c].width = 10
 
    def write_title_row(ws, row, text):
        ws.row_dimensions[row].height = 20
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=16)
        c = ws.cell(row, 2, value=text)
        c.fill = fill_(DARK); c.font = fnt_("FFFFFF", bold=True, sz=11)
        c.alignment = Alignment(horizontal='left', vertical='center')
        return row + 1
 
    def write_meta(ws, row, label, value, is_src=False):
        ws.row_dimensions[row].height = 28
        lc = ws.cell(row, 2, value=label)
        lc.font = fnt_(DARK, bold=True)
        lc.alignment = Alignment(horizontal='left', vertical='center')
        ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=16)
        vc = ws.cell(row, 3, value=value)
        vc.font = fnt_(color=LINK if is_src else "000000")
        vc.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
        return row + 1
 
    def write_interp_result(ws, row, text):
        ws.row_dimensions[row].height = 28
        lc = ws.cell(row, 2, value='Interpretación del resultado:')
        lc.font = fnt_(DARK, bold=True)
        ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=16)
        vc = ws.cell(row, 3, value=text)
        vc.font = fnt_()
        vc.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
        return row + 2
 
    def write_table(ws, row, series_dict):
        labels    = list(series_dict.keys())
        all_years = sorted(set().union(*[s.dropna().index for s in series_dict.values()]))
        hc = ws.cell(row, 2, value='Año')
        hc.fill = fill_(MED); hc.font = fnt_("FFFFFF", bold=True)
        hc.alignment = Alignment(horizontal='center', vertical='center')
        for j, lbl in enumerate(labels):
            cc = ws.cell(row, 3+j, value=lbl)
            cc.fill = fill_(MED); cc.font = fnt_("FFFFFF", bold=True)
            cc.alignment = Alignment(horizontal='center', vertical='center')
        data_start = row + 1
        for i, yr in enumerate(all_years):
            r  = data_start + i
            bg = LIGHT if i % 2 else None
            ws.row_dimensions[r].height = 15
            yc = ws.cell(r, 2, value=int(yr) if str(yr).isdigit() else yr)
            yc.alignment = Alignment(horizontal='center', vertical='center')
            if bg: yc.fill = fill_(bg)
            for j, lbl in enumerate(labels):
                s   = series_dict[lbl]
                val = s.get(yr) if yr in s.index else None
                vc  = ws.cell(r, 3+j, value=val)
                if bg: vc.fill = fill_(bg)
                if isinstance(val, (float, int)) and val is not None:
                    vc.number_format = '#,##0.0000'
                    vc.alignment = Alignment(horizontal='right', vertical='center')
        return data_start, len(all_years), data_start + len(all_years)
 
    CHART_W     = 25
    CHART_H     = 15
    CHART_FILAS = 28
 
    _COLORS  = ['2E75B6', 'C00000', '548235', 'ED7D31', '7030A0',
                '00B0F0', 'FF0066', '7F6000', '404040', 'BF9000']
    _MARKERS = ['circle', 'square', 'triangle', 'diamond',
                'star',   'plus',   'x',        'dash']
 
    def _smart_numfmt(all_vals, ya_en_miles=False):
        vals_clean = [v for v in (all_vals or []) if v is not None and not pd.isna(v)]
        if not vals_clean or max(abs(v) for v in vals_clean) == 0:
            return '#,##0.00', ''
        if max(abs(v) for v in vals_clean) <= 2:
            return '0.000', ''
        return '#,##0', ''
 
    def make_chart(ws, data_start, n_years, n_series, titulo, anchor,
                   ylabel='', all_vals=None):
        chart        = LineChart()
        chart.title  = titulo[:70] if titulo else ''
        chart.style  = 10
        chart.width  = CHART_W
        chart.height = CHART_H
 
        num_fmt, unidad = _smart_numfmt(all_vals or [])
        y_label = f'{ylabel} {unidad}'.strip() if ylabel else unidad
 
        chart.layout = Layout(
            manualLayout=ManualLayout(
                xMode='edge', yMode='edge',
                x=0.10, y=0.10, w=0.85, h=0.72,
            )
        )
 
        chart.x_axis.title       = 'Año'
        chart.x_axis.numFmt      = '0'
        chart.x_axis.tickLblPos  = 'low'
        chart.x_axis.delete      = False
        skip = max(1, n_years // 8)
        chart.x_axis.tickMarkSkip = skip
        chart.x_axis.tickLblSkip  = skip
 
        chart.y_axis.title      = y_label
        chart.y_axis.numFmt     = num_fmt
        chart.y_axis.tickLblPos = 'nextTo'
        chart.y_axis.delete     = False
 
        try:
            txPr = chart.y_axis.title.tx.rich
            if txPr.p:
                p = txPr.p[0]
                p._p.attrib.clear()
                for r_elem in p._p.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}r'):
                    for rPr in r_elem.findall('{http://schemas.openxmlformats.org/drawingml/2006/main}rPr'):
                        r_elem.remove(rPr)
                    ns = 'http://schemas.openxmlformats.org/drawingml/2006/main'
                    rPr = etree.SubElement(r_elem, f'{{{ns}}}rPr')
                    rPr.set('lang', 'es-MX')
                    rPr.set('i', '0')
                    rPr.set('b', '0')
                    rPr.set('sz', '1000')
                    r_elem.insert(0, rPr)
        except Exception:
            pass
 
        data = Reference(ws, min_col=3, max_col=2+n_series,
                         min_row=data_start-1, max_row=data_start+n_years-1)
        chart.add_data(data, titles_from_data=True, from_rows=False)
        cats = Reference(ws, min_col=2, min_row=data_start,
                         max_row=data_start+n_years-1)
        chart.set_categories(cats)
 
        if n_series > 1:
            chart.legend.position = 'b'
        else:
            chart.legend = None
 
        for i, s in enumerate(chart.series):
            color  = _COLORS[i % len(_COLORS)]
            marker = _MARKERS[i % len(_MARKERS)]
            s.smooth = True
            s.graphicalProperties.line.solidFill = color
            s.graphicalProperties.line.width     = 22000
            s.marker.symbol = marker
            s.marker.size   = 5
            s.marker.graphicalProperties.solidFill      = color
            s.marker.graphicalProperties.line.solidFill = color
 
        ws.add_chart(chart, anchor)
 
    def escribir_bloque(ws, row, title, meta_key, series_dict,
                        fuente_label, interp_text, ylabel=None, charttitle=None):
        if ylabel == CB_TON and meta_key in ('Comercio', 'Produccion', 'Consumo Aparente', 'Balanza Comercial','Dependencia de Importaciones'):
            series_dict = {k: s / 1000 for k, s in series_dict.items()}
        row = write_title_row(ws, row, title)
        row = write_meta(ws, row, 'Fórmula:',        META[meta_key]['formula'])
        row = write_meta(ws, row, 'Variables:',      META[meta_key]['variables'])
        row = write_meta(ws, row, 'Interpretación:', META[meta_key]['interp'])
        row = write_meta(ws, row, 'Fuente:', fuente_label, is_src=True)
        if meta_key in ('Comercio', 'Produccion'):
            row = write_meta(ws, row, 'Cuantía:', ylabel)
        else:
            row = write_meta(ws, row, 'Calculado con:', ylabel)
        row += 1
        d_start, n_yr, next_row = write_table(ws, row, series_dict)
 
        all_vals = [v for s in series_dict.values()
                    for v in s.dropna().values
                    if v is not None and not pd.isna(v)]
 
        ytitle = charttitle if charttitle else ylabel
        make_chart(ws, d_start, n_yr, len(series_dict), title,
                   f'H{d_start}', ytitle, all_vals=all_vals)
 
        row = write_interp_result(ws, next_row, interp_text)
        row = max(row, d_start + CHART_FILAS)
        return row
 
    def ultimo_resultado(s, meta_key, label):
        if s is None or (hasattr(s, 'empty') and s.empty):
            return f'Sin datos para {label}.'
        yr = s.index.max(); val = s.loc[yr]
        if pd.isna(val): return f'Sin valor en {yr}.'
        fn = META[meta_key].get('result_fn')
        return (f'En {yr} = {round(float(val),4)} → {fn(float(val))}'
                if fn else f'Último año: {yr}.')
 
    # ── Builders de datos ─────────────────────────────────────────────────────
    _sufijos_excel = _sufijos_auto(productos)
    def build_igll(producto, pais, col, agregado):
        pg = _sufijos_excel.get(producto, producto)
        try:
            df = igll[pg][pais][agregado]
            return df[col].dropna() if df is not None and col in df.columns else None
        except KeyError: return None
 
    def build_lafay(producto, pais, col):
        try:
            df = lafay[producto][pais]
            return df[col].dropna() if df is not None and col in df.columns else None
        except KeyError: return None
 
    def build_conc(producto, pais, flujo, col):
        try:
            df = conc[pais][flujo][producto]
            return df[col].dropna() if df is not None and col in df.columns else None
        except KeyError: return None
 
    def build_krugman_par(producto, par):
        try:
            s = ik[producto][par]
            return s.dropna() if s is not None else None
        except KeyError: return None
 
    def build_bal(producto, pais, indicador, col):
        if balance_cd is None: return None
        try:
            df = balance_cd[indicador][pais][producto]
            return df[col].dropna() if df is not None and col in df.columns else None
        except KeyError: return None
 
    def build_series_produccion(producto):
        data_ton, data_ipc = {}, {}
        for p in paises:
            key = f'P{p}{producto}'
            if key not in dicDfs: continue
            df = dicDfs[key].set_index('Año')
            if col_ton in df.columns: data_ton[np_[p]] = df[col_ton].dropna()
            if col_ipc in df.columns: data_ipc[np_[p]] = df[col_ipc].dropna()
        return data_ton, data_ipc
 
    def build_total_sumar(flujo, pais):
        data_ton, data_ipc = {}, {}
        key = f'df{flujo}T_{pais}'
        if key not in resultado_sumar: return {}, {}
        df  = resultado_sumar[key].set_index('Año')
        lbl = f'{np_[pais]} — Total'
        if col_ton in df.columns: data_ton[lbl] = df[col_ton].dropna()
        if col_ipc in df.columns: data_ipc[lbl] = df[col_ipc].dropna()
        return data_ton, data_ipc
 
    # ══════════════════════════════════════════════════════════════════════════
    # NUEVO: Helpers para extraer series de porcentajes
    # ══════════════════════════════════════════════════════════════════════════
    def _extraer_series_pct(pct_dict, producto, filtro_pais, tipo):
        """
        Extrae series del dict pct filtradas por país y tipo (exporta/importa).
        Solo devuelve las claves donde el país del Excel es el SUJETO,
        es decir que la clave empieza con "NombrePaís exporta" o
        "NombrePaís importa". Así se obtienen exactamente 2 series
        (una por cada socio T-MEC).
        """
        series = {}
        if pct_dict is None or producto not in pct_dict:
            return series
        # ej. prefijo = "Canadá exporta", sufijo tras "a " o "de " = nombre del socio
        prefijo = f'{filtro_pais} {tipo}'   # "Canadá exporta" o "Canadá importa"
        sep = ' a ' if tipo == 'exporta' else ' de '
        for key, df_pct in pct_dict[producto].items():
            if key.startswith(prefijo):
                s = df_pct[col_ton].dropna()
                if not s.empty:
                    # Extraer solo el nombre del socio: "Canadá exporta a EE.UU." → "EE.UU."
                    socio = key.split(sep, 1)[-1] if sep in key else key
                    series[socio] = s
        return series
 
    # ── Hoja por producto ─────────────────────────────────────────────────────
    def escribir_hoja_producto(wb, producto, pais_ref):
        pro_label = nprod[producto]
        pais_label = np_[pais_ref]
        ws = wb.create_sheet(title=pro_label)
        setup_cols(ws)
        row = 1
 
        # ── IGLL ─────────────────────────────────────────────────────────────
        for g in ['T-MEC', 'Mundiales']:
            for ck, cl, desc in [(col_ipc, CB_IPC, 'Valor'), (col_ton, CB_TON, 'Volumen')]:
                s = build_igll(producto, pais_ref, ck, g)
                if s is None or s.empty: continue
                row = escribir_bloque(ws, row,
                    f'IGLL — {pais_label} — {pro_label} ({g}) — {desc}',
                    'IGLL', {pais_label: s}, META['IGLL']['fuente'],
                    ultimo_resultado(s, 'IGLL', pais_label), cl, 'IGLL')
 
        # ── LAFAY ────────────────────────────────────────────────────────────
        for ck, cl, desc in [('Lafay_ipc', CB_IPC, 'Valor'), ('Lafay_ton', CB_TON, 'Volumen')]:
            s = build_lafay(producto, pais_ref, ck)
            if s is None or s.empty: continue
            row = escribir_bloque(ws, row,
                f'Índice de Lafay — {pais_label} — {pro_label} — {desc}',
                'Lafay', {pais_label: s}, META['Lafay']['fuente'],
                ultimo_resultado(s, 'Lafay', pais_label), cl, 'Lafay')
 
        # ── CONCENTRACIÓN ────────────────────────────────────────────────────
        for flujo, fl_lbl in [('X', 'Exportaciones'), ('M', 'Importaciones')]:
            ck = f'Conc_{flujo}_ipc'
            s  = build_conc(producto, pais_ref, flujo, ck)
            if s is None or s.empty: continue
            row = escribir_bloque(ws, row,
                f'Concentración ({fl_lbl}) — {pais_label} — {pro_label}',
                'Conc', {pais_label: s}, META['Conc']['fuente'],
                ultimo_resultado(s, 'Conc', pais_label), CB_IPC, f'Concentración')
 
        # ── KRUGMAN ──────────────────────────────────────────────────────────
        series_k = {}
        for par in pares_x_p[pais_ref]:
            s = build_krugman_par(producto, par)
            if s is not None and not s.empty:
                series_k[pl[par]] = s
        if series_k:
            row = escribir_bloque(ws, row,
                f'Índice de Krugman — {pais_label} — {pro_label}',
                'Krugman', series_k, META['Krugman']['fuente'],
                ultimo_resultado(list(series_k.values())[0], 'Krugman', pais_label), CB_IPC, 'Krugman')
 
        # ── CONSUMO / DEPENDENCIA ────────────────────────────────────────────
        if balance_cd:
            for ind_key in ['Consumo Aparente', 'Dependencia de Importaciones', 'Balanza Comercial']:
                for ck, cl, desc in [(col_ton, CB_TON, 'Volumen'), (col_ipc, CB_IPC, 'Valor')]:
                    s = build_bal(producto, pais_ref, ind_key, ck)
                    if s is None or s.empty: continue
                    row = escribir_bloque(ws, row,
                        f'{ind_key} — {pais_label} — {pro_label} — {desc}',
                        ind_key, {pais_label: s}, META[ind_key]['fuente'],
                        ultimo_resultado(s, ind_key, pais_label), ylabel=cl)
 
        # ── COMERCIO ─────────────────────────────────────────────────────────
        for ck, cl, desc in [(col_ton, CB_TON, 'Volumen'), (col_ipc, CB_IPC, 'Valor')]:
            series_exp, series_imp = {}, {}
            for orig, dest in pares_com_x_pais[pais_ref]:
                key = f'df{orig}{dest}{producto}'
                if key not in dicDfs: continue
                df  = dicDfs[key].set_index('Año')
                if ck not in df.columns: continue
                lbl = f'{np_com[orig]}→{np_com[dest]}'
                if orig == pais_ref:
                    series_exp[lbl] = df[ck].dropna()
                else:
                    series_imp[lbl] = df[ck].dropna()
 
            if series_exp:
                row = escribir_bloque(ws, row,
                    f'Exportaciones — {pais_label} — {pro_label} — {desc}',
                    'Comercio', series_exp, META['Comercio']['fuente'],
                    f'Exportaciones de {pro_label} de {pais_label}.', cl)
            if series_imp:
                row = escribir_bloque(ws, row,
                    f'Importaciones — {pais_label} — {pro_label} — {desc}',
                    'Comercio', series_imp, META['Comercio']['fuente'],
                    f'Importaciones de {pro_label} de {pais_label}.', cl)
 
        # ── PRODUCCIÓN ───────────────────────────────────────────────────────
        data_ton, data_ipc = build_series_produccion(producto)
        for datos, cl, desc in [(data_ton, CB_TON, 'Volumen'), (data_ipc, CB_IPC, 'Valor')]:
            if not datos: continue
            row = escribir_bloque(ws, row,
                f'Producción — {pro_label} — {desc}',
                'Produccion', datos, META['Produccion']['fuente'],
                f'Producción anual de {pro_label} en países T-MEC.', cl)
 
        # ══════════════════════════════════════════════════════════════════════
        # NUEVO: % DISTRIBUCIÓN T-MEC en la hoja del producto
        # ══════════════════════════════════════════════════════════════════════
        if porcentajes and producto in porcentajes:
            for tipo, tipo_label in [('exporta', 'Exportaciones'), ('importa', 'Importaciones')]:
                series = _extraer_series_pct(porcentajes, producto, pais_label, tipo)
                if series:
                    row = escribir_bloque(ws, row,
                        f'% T-MEC ({tipo_label}) — {pais_label} — {pro_label}',
                        'Pct T-MEC', series, META['Pct T-MEC']['fuente'],
                        f'Distribución de {tipo_label.lower()} de {pro_label} entre socios T-MEC.',
                        ylabel='Porcentaje (%)', charttitle=f'% {tipo_label}')
 
        print(f'  ✅ {pro_label} — {pais_label}')
 
    # ── Hoja Total ────────────────────────────────────────────────────────────
    def escribir_hoja_total(wb, pais):
        ws  = wb.create_sheet(title='Total')
        setup_cols(ws)
        row = 1
        pl_ = np_[pais]
 
        # IGLL
        for g in ['T-MEC', 'Mundiales']:
            for ck, cl, desc in [(col_ipc, CB_IPC, 'Valor'), (col_ton, CB_TON, 'Volumen')]:
                series = {}
                for p in paises:
                    s = build_igll('T', p, ck, g)
                    if s is not None and not s.empty:
                        series[np_[p]] = s
                if not series: continue
                row = escribir_bloque(ws, row,
                    f'IGLL Total ({g}) — {desc}',
                    'IGLL', series, META['IGLL']['fuente'],
                    ultimo_resultado(series.get(pl_), 'IGLL', pl_),
                    ylabel=cl, charttitle='IGLL')
 
        # Lafay
        for ck, cl, desc in [('Lafay_ipc', CB_IPC, 'Valor'), ('Lafay_ton', CB_TON, 'Volumen')]:
            series = {}
            for p in paises:
                s = build_lafay('T', p, ck)
                if s is not None and not s.empty:
                    series[np_[p]] = s
            if not series: continue
            row = escribir_bloque(ws, row,
                f'Índice de Lafay Total — {desc}',
                'Lafay', series, META['Lafay']['fuente'],
                ultimo_resultado(series.get(pl_), 'Lafay', pl_),
                ylabel=cl, charttitle='Índice de Lafay')
 
        # Concentración
        for flujo, fl_lbl in [('X', 'Exportaciones'), ('M', 'Importaciones')]:
            ck = f'Conc_{flujo}_ipc'
            series = {}
            for p in paises:
                s = build_conc('T', p, flujo, ck)
                if s is not None and not s.empty:
                    series[np_[p]] = s
            if not series: continue
            row = escribir_bloque(ws, row,
                f'Concentración Total ({fl_lbl})',
                'Conc', series, META['Conc']['fuente'],
                ultimo_resultado(series.get(pl_), 'Conc', pl_),
                ylabel=CB_IPC, charttitle='Concentración')
 
        # Krugman
        series_k = {}
        for p in paises:
            for par in pares_x_p[pais]:
                s = build_krugman_par('T', par)
                if s is not None and not s.empty:
                    series_k[pl[par]] = s
        if series_k:
            row = escribir_bloque(ws, row,
                f'Índice de Krugman Total',
                'Krugman', series_k, META['Krugman']['fuente'],
                ultimo_resultado(list(series_k.values())[0], 'Krugman', 'Total'), CB_IPC, 'Krugman')
 
        # Consumo / Dependencia total
        if balance_cd:
            for ind_key in ['Consumo Aparente', 'Dependencia de Importaciones', 'Balanza Comercial']:
                for ck, cl, desc in [(col_ton, CB_TON, 'Volumen'), (col_ipc, CB_IPC, 'Valor')]:
                    series = {}
                    for p in paises:
                        s = build_bal('T', p, ind_key, ck)
                        if s is not None and not s.empty:
                            series[np_[p]] = s
                    if not series: continue
                    row = escribir_bloque(ws, row,
                        f'{ind_key} — {desc}',
                        ind_key, series, META[ind_key]['fuente'],
                        ultimo_resultado(series.get(pl_), ind_key, pl_),
                        ylabel=cl)
 
        # Comercio total
        for fk, fl_lbl in [('EW', 'Exportaciones al mundo'), ('MW', 'Importaciones del mundo')]:
            series_ton, series_ipc = {}, {}
            for p in paises:
                d_ton, d_ipc = build_total_sumar(fk, p)
                if d_ton: series_ton.update(d_ton)
                if d_ipc: series_ipc.update(d_ipc)
            for series, cl, desc in [(series_ton, CB_TON, 'Volumen'), (series_ipc, CB_IPC, 'Valor')]:
                if not series: continue
                row = escribir_bloque(ws, row,
                    f'Comercio Total ({fl_lbl}) — {desc}',
                    'Comercio', series, META['Comercio']['fuente'],
                    'Total Maíz + Sorgo + Alfalfa.', cl)
 
        # Producción total
        series_ton, series_ipc = {}, {}
        for p in paises:
            d_ton, d_ipc = build_total_sumar('P', p)
            if d_ton: series_ton.update(d_ton)
            if d_ipc: series_ipc.update(d_ipc)
        for series, cl, desc in [(series_ton, CB_TON, 'Volumen'), (series_ipc, CB_IPC, 'Valor')]:
            if not series: continue
            row = escribir_bloque(ws, row,
                f'Producción Total — {desc}',
                'Produccion', series, META['Produccion']['fuente'],
                'Producción total de los 3 productos en T-MEC.', cl)
 
        # ══════════════════════════════════════════════════════════════════════
        # NUEVO: % DISTRIBUCIÓN T-MEC en la hoja Total
        # ══════════════════════════════════════════════════════════════════════
        if porcentajes and 'Total' in porcentajes:
            for tipo, tipo_label in [('exporta', 'Exportaciones'), ('importa', 'Importaciones')]:
                series = _extraer_series_pct(porcentajes, 'Total', pl_, tipo)
                if series:
                    row = escribir_bloque(ws, row,
                        f'% T-MEC ({tipo_label}) — Total',
                        'Pct T-MEC', series, META['Pct T-MEC']['fuente'],
                        f'Distribución del total entre socios T-MEC.',
                        ylabel='Porcentaje (%)', charttitle=f'% {tipo_label}')
 
        print(f'  ✅ Total — {pl_}')
 
    # ══════════════════════════════════════════════════════════════════════════
    # NUEVA HOJA: % Total Agropecuario
    # ──────────────────────────────────────────────────────────────────────────
    # Para cada par bilateral del país de este Excel, muestra qué % del total
    # agropecuario (dfAG) representa cada producto. Usa flechas:
    #   → para exportaciones del país,  ← para importaciones al país.
    # ══════════════════════════════════════════════════════════════════════════
    def escribir_hoja_pct_agropecuario(wb, pais_ref):
        if porcentajes_ag is None or not porcentajes_ag:
            return
 
        pais_label = np_[pais_ref]
        ws = wb.create_sheet(title='% Total Agropecuario')
        setup_cols(ws)
        row = 1
 
        todos = productos + ['Total']
        socios = [p for p in paises if p != pais_ref]
 
        # ── Exportaciones: País → Socio ──────────────────────────────────────
        for socio in socios:
            socio_label = np_[socio]
            par_key = f'{pais_label}→{socio_label}'
 
            series_par = {}
            for producto in todos:
                pro_label = nprod.get(producto, producto)
                if producto in porcentajes_ag and par_key in porcentajes_ag[producto]:
                    s = porcentajes_ag[producto][par_key][col_ton].dropna()
                    if not s.empty:
                        series_par[pro_label] = s
 
            if series_par:
                row = escribir_bloque(ws, row,
                    f'% Agropecuario — Exp. {pais_label}→{socio_label}',
                    'Pct Agropecuario', series_par, META['Pct Agropecuario']['fuente'],
                    f'Participación de cada producto en las exportaciones agropecuarias de {pais_label} a {socio_label}.',
                    ylabel='Porcentaje (%)', charttitle='% del total agropecuario')
 
        # ── Importaciones: País ← Socio ──────────────────────────────────────
        for socio in socios:
            socio_label = np_[socio]
            par_key = f'{pais_label}←{socio_label}'
 
            series_par = {}
            for producto in todos:
                pro_label = nprod.get(producto, producto)
                if producto in porcentajes_ag and par_key in porcentajes_ag[producto]:
                    s = porcentajes_ag[producto][par_key][col_ton].dropna()
                    if not s.empty:
                        series_par[pro_label] = s
 
            if series_par:
                row = escribir_bloque(ws, row,
                    f'% Agropecuario — Imp. {pais_label}←{socio_label}',
                    'Pct Agropecuario', series_par, META['Pct Agropecuario']['fuente'],
                    f'Participación de cada producto en las importaciones agropecuarias de {pais_label} desde {socio_label}.',
                    ylabel='Porcentaje (%)', charttitle='% del total agropecuario')
 
        if row > 1:
            print(f'  ✅ % Total Agropecuario — {pais_label}')
        else:
            print(f'  ⬛ Sin datos de % agropecuario para {pais_label}')
 
    # ── Hoja Fuentes ──────────────────────────────────────────────────────────
    def agregar_hoja_fuentes(wb):
        ws_f = wb.create_sheet('Fuentes')
        ws_f.column_dimensions['B'].width = 20
        ws_f.column_dimensions['C'].width = 90
        for col, val in [(2, 'Indicador'), (3, 'Fuente')]:
            c = ws_f.cell(1, col, value=val)
            c.fill = fill_(DARK); c.font = fnt_("FFFFFF", bold=True)
        for i, (ind, cita) in enumerate([
            ('IGLL',                    cita_gral),
            ('Lafay',                   cita_laf),
            ('Concentración',           cita_gral),
            ('Krugman',                 cita_gral),
            ('Comercio',                cita_gral),
            ('Producción',              cita_laf),
            ('% Distribución T-MEC',    cita_gral),        # ← NUEVO
            ('% Total Agropecuario',    cita_gral),        # ← NUEVO
        ], start=2):
            ws_f.cell(i, 2, ind)
            vc = ws_f.cell(i, 3, cita)
            vc.alignment = Alignment(wrap_text=True, vertical='top')
            ws_f.row_dimensions[i].height = 42
 
    # ── Loop principal ─────────────────────────────────────────────────────────
    with zipfile.ZipFile(
            os.path.join(ruta_base, 'DataBaseLDSG_PorPais.zip'), 'w') as z:
 
        for pais in paises:
            wb = Workbook()
            wb.remove(wb.active)
 
            for producto in productos:
                escribir_hoja_producto(wb, producto, pais)
 
            escribir_hoja_total(wb, pais)
            escribir_hoja_pct_agropecuario(wb, pais)     # ← NUEVA HOJA
            agregar_hoja_fuentes(wb)
 
            buf = io.BytesIO()
            wb.save(buf)
            z.writestr(f'{np_[pais]}.xlsx', buf.getvalue())
            print(f'✅ {np_[pais]}.xlsx')
 
    print('✅ ZIP generado: DataBaseLDSG_PorPais.zip')
#--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

def cargar_datos(productos: dict, ipc_path=None, min_años=1) -> dict:
    """
    Carga datos, deflacta y calcula todos los indicadores en una sola llamada.
    """
    RUTA_BASE   = os.path.dirname(os.getcwd())
    ruta_total  = os.path.join(RUTA_BASE, 'Comercio_Total')
 
    # ── Productos agropecuarios ───────────────────────────────────────────────
    dicDfs  = {}
    dfs     = []
    nombres = []
 
    for producto, carpeta in productos.items():
        ruta = os.path.join(RUTA_BASE, carpeta)
        print(f'\n━━━ {producto} ━━━')
        d, l, n = cargar_dicDfs(ruta, producto)
        dicDfs.update(d)
        dfs    += l
        nombres += n
 
    # ── Comercio total ────────────────────────────────────────────────────────
    pares_total = [
        ('TCM', 'TOTAL_CA_MX.csv'),  ('TCU', 'TOTAL_CA_USA.csv'), ('TCW', 'TOTAL_CA_W.csv'),
        ('TMC', 'TOTAL_MX_CA.csv'),  ('TMU', 'TOTAL_MX_USA.csv'), ('TMW', 'TOTAL_MX_W.csv'),
        ('TUC', 'TOTAL_USA_CA.csv'), ('TUM', 'TOTAL_USA_MX.csv'), ('TUW', 'TOTAL_USA_W.csv'),
        ('TWC', 'TOTAL_W_CA.csv'),   ('TWU', 'TOTAL_W_USA.csv'),  ('TWM', 'TOTAL_W_MX.csv'),
    ]
 
    print('\n━━━ Comercio total ━━━')
    dictT    = {}
    dfst     = []
    nombrest = []
 
    for key, archivo in pares_total:
        ruta = os.path.join(ruta_total, archivo)
        if os.path.exists(ruta):
            try:
                df = pd.read_csv(ruta)
                dictT[key] = df
                dfst.append(df)
                nombrest.append(key)
                print(f'✅ {key} ← {archivo}')
            except Exception as e:
                print(f'⚠️  {key}: {e}')
        else:
            print(f'⬛ No existe: {archivo}')
 
    # ── AG ────────────────────────────────────────────────────────────────────
    print('\n━━━ AG ━━━')
    try:
        dfAG = pd.read_excel(
            os.path.join(ruta_total, 'comtrade_limpio.xlsx'),
            sheet_name='Deflactados', header=None, skiprows=2
        )
        dfAG.columns = ['_', 'Año', 'Export_CA', 'Export_MX', 'Export_USA',
                                     'Import_CA', 'Import_MX', 'Import_USA']
        dfAG = dfAG[['Año', 'Export_CA', 'Export_MX', 'Export_USA',
                     'Import_CA', 'Import_MX', 'Import_USA']].dropna(subset=['Año'])
        print('✅ dfAG cargado')
    except Exception as e:
        print(f'⚠️  dfAG: {e}')
        dfAG = pd.DataFrame()
 
    # ── Deflación ──────────────────────────────────────────────────────────────
    indicadores = None
    lista_productos = list(productos.keys())
 
    if ipc_path is not None:
        print('\n━━━ Deflación ━━━')
        valor_real(dfs, nombres, min_años, ipc_path=ipc_path)
 
        # ── Indicadores ──────────────────────────────────────────────────────
        print('\n━━━ Indicadores ━━━')
        indicadores = calcular_indicadores(
            nombres, dfs, dicDfs, dfAG, productos=lista_productos
        )
 
        # ── Exportar Excel (ahora con porcentajes) ───────────────────────────
        print('\n━━━ Exportar Excel ━━━')
        ruta_excel = os.path.join(RUTA_BASE, 'Indicadores')
        exportar_excel_por_pais(
            indicadores['igll'],
            indicadores['lafay'],
            indicadores['concentracion'],
            indicadores['krugman'],
            dicDfs,
            balance_cd=indicadores['balance_consumo_dependencia'],
            porcentajes=indicadores['porcentajes_temec'],        # ← NUEVO
            porcentajes_ag=indicadores['porcentajes_ag'],        # ← NUEVO
            ruta_base=ruta_excel,
            productos=lista_productos,
        )
    else:
        print('\n⚠️ No se pasó ipc_path — datos sin deflactar, indicadores no calculados.')
 
    return {
        'dicDfs'      : dicDfs,
        'dfs'         : dfs,
        'nombres'     : nombres,
        'dictT'       : dictT,
        'dfst'        : dfst,
        'nombrest'    : nombrest,
        'dfAG'        : dfAG,
        'indicadores' : indicadores,
    }
 
 
#==================================================================================================================================================================================================================

def calcular_indicadores(nombres, dfs, dicDfs, dfAG, productos=None):
    """
    Wrapper que llama a todos los indicadores en secuencia.
    """
    cols = set()
    for df in dfs:
        cols.update(df.columns.tolist())
    print(f'Columnas disponibles: {cols}')
 
    igll   = IGLL(nombres, dfs, productos=productos)
    lafay  = lafay_anual(dicDfs, productos=productos)
    conc   = concentracion(dicDfs, dfAG, productos=productos)
    ik     = krugman(dicDfs, dfAG, productos=productos)
    bcd    = balance_consumo_dependencia(dicDfs, productos=productos)
    pct, pct_ag = porcentajes_temec(nombres, dfs, dfAG=dfAG, productos=productos)
 
    print('\n✅ Todos los indicadores calculados')
    return {
        'igll': igll,
        'lafay': lafay,
        'concentracion': conc,
        'krugman': ik,
        'balance_consumo_dependencia': bcd,
        'porcentajes_temec': pct,
        'porcentajes_ag': pct_ag,
    }