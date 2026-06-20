"""
Sistema de Rutas de Emergencia para Guayaquil
Algoritmo: Floyd-Warshall con datos automáticos de OpenStreetMap
Zona: Guayaquil completo con énfasis en Ceibos + ESPOL
Autores: Fátima Torres y Jack Vera
"""

import tkinter as tk
from tkinter import ttk, messagebox
import folium
import webbrowser
import os
import math
import requests
import json
import time
from typing import Dict, List, Tuple, Optional, Set
import threading
from dataclasses import dataclass
import networkx as nx

# ========================
# Configuración y utilidades
# ========================

@dataclass
class ServicioEmergencia:
    nombre: str
    tipo: str
    coordenadas: Tuple[float, float]
    telefono: str
    especialidad: str = ""
    zona: str = ""

class DescargadorRed:
    """
    Descarga automática de la red vial de Guayaquil usando Overpass API (OpenStreetMap)
    """
    def __init__(self):
        self.overpass_url = "http://overpass-api.de/api/interpreter"
        
        # Bounding box de Guayaquil (amplio para incluir toda la zona urbana)
        # [sur, oeste, norte, este]
        self.bbox_guayaquil = [-2.19, -79.97, -2.142, -79.920]  # Ceibos+ESPOL box (forced)
        
        # Bounding box de Ceibos + ESPOL (área prioritaria)
        self.bbox_ceibos = [-2.19, -79.97, -2.142, -79.920]  # Ceibos+ESPOL box
    
    def descargar_red_vial(self, usar_solo_ceibos=False) -> Dict:
        """
        Descarga la red vial usando Overpass API
        """
        bbox = self.bbox_ceibos if usar_solo_ceibos else self.bbox_guayaquil
        
        # Query Overpass para obtener calles principales
        query = f"""
[out:json][timeout:120];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|service|living_street|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link)$"]
      ({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
  way["highway"="living_street"]
      ({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
);
out tags geom;
"""
        
        try:
            print("🌐 Descargando red vial desde OpenStreetMap...")
            response = requests.post(self.overpass_url, 
                                   data=query, 
                                   timeout=180,
                                   headers={'User-Agent': 'EmergencySystem/1.0'})
            
            if response.status_code != 200:
                raise Exception(f"Error en Overpass API: {response.status_code}")
                
            return response.json()
            
        except Exception as e:
            print(f"❌ Error descargando red vial: {e}")
            return self._red_fallback()
    
    def _red_fallback(self) -> Dict:
        """Red de respaldo en caso de fallo en la descarga"""
        print("🔄 Usando red de respaldo...")
        return {
            "elements": [
                {
                    "type": "way",
                    "id": 1,
                    "geometry": [
                        {"lat": -2.1448, "lon": -79.9663},  # ESPOL
                        {"lat": -2.1672, "lon": -79.9378},  # Ceibos Centro
                    ]
                }
            ]
        }
    
    def procesar_red_osm(self, data_osm: Dict) -> Dict:
        """
        Convierte datos OSM en estructura de grafo para Floyd-Warshall
        """
        print("🔄 Procesando red vial...")
        MAX_NODOS = 5000  # límite duro de nodos en la red procesada
        # Pre-conteo de puntos para decidir submuestreo
        total_points = 0
        for _el in data_osm.get("elements", []):
            if _el.get("type") == "way" and "geometry" in _el:
                total_points += len(_el["geometry"])
        # Reservamos ~150 nodos para servicios y referencias
        objetivo = max(500, MAX_NODOS - 150)
        downsample_step = 1
        if total_points > objetivo:
            # Elegimos un paso que lleve el total por debajo del objetivo
            # Añadimos un +10% de margen por intersecciones compartidas
            import math
            downsample_step = max(1, math.ceil((total_points * 1.1) / objetivo))
            print(f"⚖️  Submuestreo activado: cada {{downsample_step}} puntos (total≈{{total_points}} → objetivo≤{{objetivo}})")
        
        nodos = {}
        aristas = []
        nodo_counter = 0
        coord_to_id = {}
        
        for elemento in data_osm.get("elements", []):
            if elemento["type"] == "way" and "geometry" in elemento:
                geometry = elemento["geometry"]
                
                # === NUEVO: leer tags para decidir direccionalidad ===
                tags = elemento.get("tags", {})
                oneway = str(tags.get("oneway", "no")).lower()
                junction = str(tags.get("junction", "")).lower()
            
                # por defecto, doble vía
                dir_mode = "both"
                if oneway in ("yes", "true", "1"):
                    dir_mode = "forward"     # en el orden de la geometría
                elif oneway == "-1":
                    dir_mode = "backward"    # sentido invertido al de la geometría
                elif junction == "roundabout":
                    dir_mode = "forward" 
                       
                # Crear nodos para cada punto de la geometría
                way_nodes = []
                for _idx, punto in enumerate(geometry):
                    if (downsample_step > 1) and (_idx % downsample_step != 0) and (_idx != len(geometry) - 1):
                        continue
                    lat, lon = punto["lat"], punto["lon"]
                    coord_key = f"{lat:.6f},{lon:.6f}"
                    
                    if coord_key not in coord_to_id:
                        node_id = f"node_{nodo_counter}"
                        coord_to_id[coord_key] = node_id
                        nodos[node_id] = (lat, lon)
                        nodo_counter += 1
                    
                    way_nodes.append(coord_to_id[coord_key])
                
                # Crear aristas entre nodos consecutivos (DIRIGIDAS según oneway)
                for i in range(len(way_nodes) - 1):
                    n1, n2 = way_nodes[i], way_nodes[i + 1]
                    if n1 != n2:  # Evitar auto-loops
                        coord1 = nodos[n1]
                        coord2 = nodos[n2]
                        distancia = max(self._haversine_km(coord1, coord2), 0.01)  # mínimo 10m

                        if dir_mode in ("both", "forward"):
                            aristas.append((n1, n2, distancia))
                        if dir_mode in ("both", "backward"):
                            aristas.append((n2, n1, distancia))

        
        print(f"✅ Red procesada: {len(nodos)} nodos, {len(aristas)} aristas")
        return {"nodos": nodos, "aristas": aristas}
    
    def _haversine_km(self, coord1: Tuple[float,float], coord2: Tuple[float,float]) -> float:
        """Calcula distancia entre dos coordenadas en km"""
        lat1, lon1 = coord1
        lat2, lon2 = coord2
        R = 6371.0  # Radio de la Tierra en km
        
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        
        a = (math.sin(dlat/2)**2 + 
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * 
             math.sin(dlon/2)**2)
        
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        return R * c

class SistemaRutasEmergenciaGuayaquil:
    """
    Sistema principal de rutas de emergencia para Guayaquil
    Implementa Floyd-Warshall sobre red real de OpenStreetMap
    """
    
    def __init__(self):
        print("🚨 Iniciando Sistema de Emergencias - Guayaquil")
        
        # Servicios de emergencia reales en Guayaquil
        self.servicios_emergencia = self._inicializar_servicios()
        
        # Ubicaciones de referencia ampliadas
        self.ubicaciones_referencia = self._inicializar_ubicaciones()
        
        # Red vial (se carga dinámicamente)
        self.red_vial = None
        self.fw_preparado = False
        
        # Descargador de red
        self.descargador = DescargadorRed()
        

    def _latlon_to_xy(self, lat, lon, lat0=None):
        # Convert lat/lon to local planar XY (km) using equirectangular approx.
        R = 6371.0
        if lat0 is None:
            lat0 = lat
        x = math.radians(lon) * R * math.cos(math.radians(lat0))
        y = math.radians(lat) * R
        return x, y

    def _xy_to_latlon(self, x, y, lat0):
        # Inverse of _latlon_to_xy (km -> lat/lon).
        R = 6371.0
        lat = math.degrees(y / R)
        lon = math.degrees(x / (R * math.cos(math.radians(lat0))))
        return lat, lon

    def _nearest_edge_projection(self, point_latlon):
        """
        Encuentra la arista más cercana y el punto proyectado sobre ella.
        Retorna (u, v, t, proj_lat, proj_lon, dist_pt_to_proj_km) con t in [0,1].
        """
        if not self.red_vial or not self.red_vial.get("aristas"):
            return None
        px, py = self._latlon_to_xy(point_latlon[0], point_latlon[1], point_latlon[0])
        best = (None, None, None, None, None, float("inf"))
        nodos = self.red_vial["nodos"]
        # Iterar sobre aristas dirigidas existentes
        for (u, v, _w) in self.red_vial["aristas"]:
            a_lat, a_lon = nodos[u]
            b_lat, b_lon = nodos[v]
            ax, ay = self._latlon_to_xy(a_lat, a_lon, point_latlon[0])
            bx, by = self._latlon_to_xy(b_lat, b_lon, point_latlon[0])
            vx, vy = bx - ax, by - ay
            wx, wy = px - ax, py - ay
            denom = vx * vx + vy * vy
            if denom <= 1e-12:
                t = 0.0
                qx, qy = ax, ay
            else:
                t = (wx * vx + wy * vy) / denom
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                qx, qy = ax + t * vx, ay + t * vy
            # distance point->proj (km)
            dx, dy = px - qx, py - qy
            d = (dx * dx + dy * dy) ** 0.5
            if d < best[5]:
                q_lat, q_lon = self._xy_to_latlon(qx, qy, point_latlon[0])
                best = (u, v, t, q_lat, q_lon, d)
        return best

    def _split_edge_and_connect_point(self, point_latlon, node_id_prefix="snap"):
        """
        Inserta un nodo sobre la arista más cercana y lo conecta al punto externo (servicio/ref).
        Devuelve (new_node_id, dist_conn_km). NO elimina la arista original; añade subdivisiones
        preservando la direccionalidad existente.
        """
        res = self._nearest_edge_projection(point_latlon)
        if not res:
            return None, None
        u, v, t, q_lat, q_lon, d_pt = res
        new_id = f"{node_id_prefix}_{len(self.red_vial['nodos'])}"
        # Registrar nuevo nodo en la red
        self.red_vial["nodos"][new_id] = (q_lat, q_lon)

        # Direccionalidad existente
        dir_set = {(a, b) for (a, b, _w) in self.red_vial["aristas"]}
        # Distancias de los segmentos
        d_uq = self.descargador._haversine_km(self.red_vial["nodos"][u], (q_lat, q_lon))
        d_qv = self.descargador._haversine_km((q_lat, q_lon), self.red_vial["nodos"][v])

        # Subdividir según direcciones presentes
        if (u, v) in dir_set:
            self.red_vial["aristas"].append((u, new_id, max(d_uq, 0.01)))
            self.red_vial["aristas"].append((new_id, v, max(d_qv, 0.01)))
        if (v, u) in dir_set:
            self.red_vial["aristas"].append((v, new_id, max(d_qv, 0.01)))
            self.red_vial["aristas"].append((new_id, u, max(d_uq, 0.01)))

        # Distancia punto ↔ nodo proyectado
        d_conn = max(self.descargador._haversine_km(point_latlon, (q_lat, q_lon)), 0.01)
        return new_id, d_conn

    def _inicializar_servicios(self) -> Dict[str, List[ServicioEmergencia]]:
        """Servicios de emergencia reales en Guayaquil"""
        return {
            'Hospital': [
                ServicioEmergencia("Hospital del IESS Los Ceibos", "Hospital", 
                                 (-2.175396, -79.941602), "(04) 380-5130", "Público"),
                ServicioEmergencia("InterHospital", "Hospital", 
                                 (-2.180693, -79.945202), "(04) 375-0000", "Privado")
            ],
            
            'Bomberos': [
                ServicioEmergencia("Cuartel Bomberos #5", "Bomberos", 
                                 (-2.16239, -79.92644), "(04) 371-4840", zona="Ceibos")
            ],
            
            'Policía': [
                ServicioEmergencia("UPC Los Ceibos", "Policía", 
                                 (-2.16567, -79.93697), "911", zona="Los Ceibos"),
                ServicioEmergencia("UPC Los Ceibos 2", "Policía", 
                                 (-2.151886, -79.952468), "911", zona="Los Ceibos")
            ]
        }
    
    def _inicializar_ubicaciones(self) -> Dict[str, Tuple[float, float]]:
        """Ubicaciones de referencia expandidas para toda Guayaquil"""
        return {
            # ESPOL y alrededores
            "ESPOL": (-2.1448, -79.9663),
            "FADCOM ESPOL": (-2.144132, -79.962161),
            "FIEC ESPOL": (-2.144503, -79.968048),
            "FCNM ESPOL": (-2.147740, -79.967923),
            "FCSH ESPOL": (-2.147568, -79.968294),
            "FCV ESPOL": (-2.152106, -79.957153),
            "FICT ESPOL": (-2.145025, -79.964813),
            "FIMCP ESPOL": (-2.144065, -79.965581),
            
            # Ceibos
            "Los Ceibos": (-2.1672, -79.9378),
            "Riocentro Ceibos": (-2.177456, -79.943431),
            "Las Cumbres": (-2.157333, -79.946304),
            "Colinas de los Ceibos": (-2.163287, -79.945786)
        }
    
    def cargar_red_vial(self, solo_ceibos: bool = False) -> bool:
        """
        Carga la red vial desde OpenStreetMap
        """
        try:
            # Descargar datos OSM
            data_osm = self.descargador.descargar_red_vial(solo_ceibos)
            
            # Procesar en estructura de grafo
            self.red_vial = self.descargador.procesar_red_osm(data_osm)
            
            # Añadir servicios de emergencia como nodos especiales
            self._integrar_servicios_en_red()
            
            return True
            
        except Exception as e:
            print(f"❌ Error cargando red vial: {e}")
            return False
    
    def _integrar_servicios_en_red(self):
        """
        Integra los servicios de emergencia como nodos en la red
        """
        if not self.red_vial:
            return
            
        print("🔄 Integrando servicios de emergencia en la red...")
        
        # Añadir servicios como nodos especiales
        for tipo_servicio, servicios in self.servicios_emergencia.items():
            for servicio in servicios:
                node_id = f"servicio_{servicio.nombre.replace(' ', '_')}"
                self.red_vial["nodos"][node_id] = servicio.coordenadas
                
                # Conectar por proyección al borde más cercano (split de arista)
                new_node, d_conn = self._split_edge_and_connect_point(servicio.coordenadas, node_id_prefix="snap")
                if new_node:
                    self.red_vial["aristas"].append((node_id, new_node, d_conn))
                    self.red_vial["aristas"].append((new_node, node_id, d_conn))

        
        # Añadir ubicaciones de referencia
        for nombre, coords in self.ubicaciones_referencia.items():
            node_id = f"ref_{nombre.replace(' ', '_')}"
            self.red_vial["nodos"][node_id] = coords
            
            # Conectar por proyección al borde más cercano (split)
            new_node, d_conn = self._split_edge_and_connect_point(coords, node_id_prefix="snap")
            if new_node and new_node != node_id:
                self.red_vial["aristas"].append((node_id, new_node, min(d_conn, 0.5)))
                self.red_vial["aristas"].append((new_node, node_id, min(d_conn, 0.5)))

    
    def _encontrar_nodo_mas_cercano(self, coordenadas: Tuple[float, float]) -> Optional[str]:
        """Encuentra el nodo más cercano en la red a unas coordenadas dadas"""
        if not self.red_vial or not self.red_vial["nodos"]:
            return None
            
        mejor_distancia = float('inf')
        mejor_nodo = None
        
        for node_id, node_coords in self.red_vial["nodos"].items():
            if node_id.startswith('servicio_') or node_id.startswith('ref_'):
                continue  # Evitar servicios en esta búsqueda
                
            distancia = self.descargador._haversine_km(coordenadas, node_coords)
            if distancia < mejor_distancia:
                mejor_distancia = distancia
                mejor_nodo = node_id
        
        return mejor_nodo
    
    def preparar_floyd_warshall(self) -> bool:
        """
        Prepara y ejecuta el algoritmo de Floyd-Warshall
        """
        if not self.red_vial:
            print("❌ Red vial no cargada")
            return False
            
        print("🔄 Preparando algoritmo Floyd-Warshall...")
        
        # Crear mapeo de nodos a índices
        nodos = list(self.red_vial['nodos'].keys())
        self.nodo_a_indice = {nodo: i for i, nodo in enumerate(nodos)}
        self.indice_a_nodo = {i: nodo for nodo, i in self.nodo_a_indice.items()}
        
        n = len(nodos)
        INF = float('inf')
        
        # Inicializar matrices de distancia y sucesores
        self.matriz_distancias = [[INF] * n for _ in range(n)]
        self.matriz_sucesores = [[None] * n for _ in range(n)]
        
        # Diagonal principal (distancia de un nodo a sí mismo = 0)
        for i in range(n):
            self.matriz_distancias[i][i] = 0.0
            self.matriz_sucesores[i][i] = i
        
        # Llenar matriz con aristas existentes
        for u, v, peso in self.red_vial['aristas']:
            if u in self.nodo_a_indice and v in self.nodo_a_indice:
                i = self.nodo_a_indice[u]
                j = self.nodo_a_indice[v]
                
                # Grafo dirigido: NO espejar j->i
                if peso < self.matriz_distancias[i][j]:
                    self.matriz_distancias[i][j] = peso
                    self.matriz_sucesores[i][j] = j

        
        # Aplicar Floyd-Warshall
        print("🔄 Ejecutando Floyd-Warshall...")
        inicio = time.time()
        
        for k in range(n):
            if k % max(1, n // 10) == 0:  # Progreso cada 10%
                progreso = (k / n) * 100
                print(f"   Progreso: {progreso:.1f}%")
            
            for i in range(n):
                if self.matriz_distancias[i][k] == INF:
                    continue
                    
                for j in range(n):
                    nueva_distancia = (self.matriz_distancias[i][k] + 
                                     self.matriz_distancias[k][j])
                    
                    if nueva_distancia < self.matriz_distancias[i][j]:
                        self.matriz_distancias[i][j] = nueva_distancia
                        self.matriz_sucesores[i][j] = self.matriz_sucesores[i][k]
        
        tiempo_total = time.time() - inicio
        print(f"✅ Floyd-Warshall completado en {tiempo_total:.2f} segundos")
        
        self.fw_preparado = True
        return True
    
    def obtener_ruta(self, origen: str, destino: str) -> Optional[Dict]:
        """
        Obtiene la ruta óptima entre dos nodos usando Floyd-Warshall
        """
        if not self.fw_preparado:
            return None
            
        if origen not in self.nodo_a_indice or destino not in self.nodo_a_indice:
            return None
        
        i = self.nodo_a_indice[origen]
        j = self.nodo_a_indice[destino]
        
        if self.matriz_sucesores[i][j] is None:
            return None  # Sin ruta
        
        # Reconstruir ruta
        ruta = [i]
        actual = i
        
        while actual != j:
            actual = self.matriz_sucesores[actual][j]
            if actual is None:
                return None
            ruta.append(actual)
        
        # Convertir índices a nombres de nodos
        ruta_nodos = [self.indice_a_nodo[idx] for idx in ruta]
        
        # Obtener coordenadas de la ruta
        coordenadas = []
        for nodo in ruta_nodos:
            lat, lon = self.red_vial["nodos"][nodo]
            coordenadas.append([lon, lat])  # GeoJSON format
        
        distancia_km = self.matriz_distancias[i][j]
        tiempo_min = (distancia_km / 25.0) * 60.0  # Asumiendo 25 km/h promedio urbano
        
        return {
            "ruta_nodos": ruta_nodos,
            "coordenadas": coordenadas,
            "distancia_km": distancia_km,
            "tiempo_min": tiempo_min
        }
    
    def buscar_ubicacion(self, texto: str) -> Optional[Tuple[float, float]]:
        """Busca una ubicación por nombre"""
        texto = texto.strip().lower()
        
        # Búsqueda exacta
        for nombre, coords in self.ubicaciones_referencia.items():
            if texto == nombre.lower():
                return coords
        
        # Búsqueda parcial
        for nombre, coords in self.ubicaciones_referencia.items():
            if texto in nombre.lower() or nombre.lower() in texto:
                return coords
        
        return None
    
    def encontrar_servicios_cercanos(self, coordenadas_origen: Tuple[float, float], 
                                   tipo_servicio: str, max_resultados: int = 3) -> List[Dict]:
        """
        Encuentra los servicios más cercanos usando Floyd-Warshall
        """
        if not self.fw_preparado or tipo_servicio not in self.servicios_emergencia:
            return []
        
        # Encontrar nodo más cercano al origen
        nodo_origen = self._encontrar_nodo_mas_cercano(coordenadas_origen)
        if not nodo_origen:
            return []
        
        resultados = []
        
        for servicio in self.servicios_emergencia[tipo_servicio]:
            nodo_servicio = f"servicio_{servicio.nombre.replace(' ', '_')}"
            
            if nodo_servicio not in self.nodo_a_indice:
                continue
            
            # Obtener ruta
            ruta_info = self.obtener_ruta(nodo_origen, nodo_servicio)
            if not ruta_info:
                continue
            
            resultados.append({
                "servicio": servicio,
                "distancia_km": ruta_info["distancia_km"],
                "tiempo_min": ruta_info["tiempo_min"],
                "ruta_coordenadas": ruta_info["coordenadas"],
                "ruta_nodos": ruta_info["ruta_nodos"]
            })
        
        # Ordenar por distancia
        resultados.sort(key=lambda x: x["distancia_km"])
        
        return resultados[:max_resultados]


class InterfazSistemaEmergencia:
    """
    Interfaz gráfica principal del sistema
    """
    
    def __init__(self):
        self.sistema = SistemaRutasEmergenciaGuayaquil()
        self.resultados_actuales = []
        self.ubicacion_usuario = None
        self.nombre_ubicacion = ""
        
        self._crear_interfaz()
        
    def _crear_interfaz(self):
        """Crea la interfaz gráfica"""
        self.root = tk.Tk()
        self.root.title("🚨 Sistema de Emergencias Guayaquil - Floyd-Warshall")
        self.root.geometry("900x800")
        self.root.configure(bg="#f0f8ff")
        
        # Estilo
        style = ttk.Style()
        style.theme_use("clam")
        
        # Header
        header_frame = tk.Frame(self.root, bg="#1e3a8a", height=100)
        header_frame.pack(fill="x")
        header_frame.pack_propagate(False)
        
        tk.Label(header_frame, 
                text="🚨 SISTEMA DE RUTAS DE EMERGENCIA\n🏙️ GUAYAQUIL - ALGORITMO FLOYD-WARSHALL",
                font=("Arial", 14, "bold"),
                bg="#1e3a8a", fg="white").pack(expand=True)
        
        # Crear canvas con scrollbar
        canvas = tk.Canvas(self.root, bg="#f0f8ff")
        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Contenido principal
        main_frame = tk.Frame(scrollable_frame, bg="#f0f8ff")
        main_frame.pack(expand=True, fill="both", padx=20, pady=20)
        
        # Sección: Cargar red
        self._crear_seccion_carga_red(main_frame)
        
        # Sección: Ubicación
        self._crear_seccion_ubicacion(main_frame)
        
        # Sección: Tipo de servicio
        self._crear_seccion_servicio(main_frame)
        
        # Botón de búsqueda
        tk.Button(main_frame, 
                 text="🔍 BUSCAR SERVICIOS DE EMERGENCIA",
                 font=("Arial", 12, "bold"),
                 bg="#dc2626", fg="white",
                 command=self.buscar_servicios,
                 height=2, relief="flat", bd=0, padx=30).pack(pady=20)
        
        # Resultados
        self._crear_seccion_resultados(main_frame)
        
        # Información del proyecto
        self._crear_seccion_info(main_frame)
        
    def _crear_seccion_carga_red(self, parent):
        """Sección para cargar la red vial"""
        frame = tk.LabelFrame(parent, text="🌐 Cargar Red Vial", 
                             font=("Arial", 11, "bold"),
                             bg="#f0f8ff", relief="groove", bd=2)
        frame.pack(fill="x", pady=15)
        
        tk.Label(frame, 
                text="Descargue la red vial de Guayaquil desde OpenStreetMap:",
                font=("Arial", 10), bg="#f0f8ff").pack(pady=10, padx=15, anchor="w")
        
        button_frame = tk.Frame(frame, bg="#f0f8ff")
        button_frame.pack(fill="x", padx=15, pady=10)
        
        tk.Button(button_frame, text="📥 Cargar Red Completa (Guayaquil)",
                 bg="#2563eb", fg="white", font=("Arial", 9, "bold"),
                 command=lambda: self.cargar_red(True))  # forzado a Ceibos+ESPOL).pack(side="left", padx=5)
        
        tk.Button(button_frame, text="📥 Cargar Ceibos + ESPOL (BOX fijado)",
                 bg="#059669", fg="white", font=("Arial", 9, "bold"),
                 command=lambda: self.cargar_red(True)).pack(side="left", padx=5)
        
        self.estado_red = tk.Label(frame, text="⚠️ Red no cargada", 
                                  font=("Arial", 9, "bold"), 
                                  bg="#f0f8ff", fg="#dc2626")
        self.estado_red.pack(pady=5, padx=15, anchor="w")
        
    def _crear_seccion_ubicacion(self, parent):
        """Sección para configurar ubicación"""
        frame = tk.LabelFrame(parent, text="📍 Su Ubicación", 
                             font=("Arial", 11, "bold"),
                             bg="#f0f8ff", relief="groove", bd=2)
        frame.pack(fill="x", pady=15)
        
        tk.Label(frame, 
                text="Ingrese su ubicación actual en Guayaquil:",
                font=("Arial", 10), bg="#f0f8ff").pack(pady=(10,5), padx=15, anchor="w")
        
        tk.Label(frame,
                text="Ejemplos: ESPOL,FIEC, FIMCP, Los Ceibos, Riocentro Ceibos, Las Cumbres, Colinas de los Ceibos ...",
                font=("Arial", 8), bg="#f0f8ff", fg="#6b7280").pack(pady=2, padx=15, anchor="w")
        
        ubicacion_frame = tk.Frame(frame, bg="#f0f8ff")
        ubicacion_frame.pack(fill="x", padx=15, pady=10)
        
        self.var_ubicacion = tk.StringVar()
        self.entry_ubicacion = tk.Entry(ubicacion_frame, textvariable=self.var_ubicacion,
                                       font=("Arial", 10), relief="solid", bd=1)
        self.entry_ubicacion.pack(side="left", fill="x", expand=True, padx=(0,10))
        
        tk.Button(ubicacion_frame, text="📍 Localizar",
                 bg="#059669", fg="white", font=("Arial", 9, "bold"),
                 command=self.localizar_usuario).pack(side="right")
        
        self.estado_ubicacion = tk.Label(frame, text="❌ Ubicación no establecida",
                                        font=("Arial", 9, "bold"),
                                        bg="#f0f8ff", fg="#dc2626")
        self.estado_ubicacion.pack(pady=5, padx=15, anchor="w")
        
    def _crear_seccion_servicio(self, parent):
        """Sección para seleccionar tipo de servicio"""
        frame = tk.LabelFrame(parent, text="🚨 Tipo de Emergencia",
                             font=("Arial", 11, "bold"),
                             bg="#f0f8ff", relief="groove", bd=2)
        frame.pack(fill="x", pady=15)
        
        self.var_servicio = tk.StringVar(value="Hospital")
        
        servicios = [
            ("Hospital", "🏥", "Emergencias médicas y hospitales"),
            ("Policía", "👮", "Seguridad y emergencias policiales"),
            ("Bomberos", "🚒", "Incendios, rescates y emergencias")
        ]
        
        for servicio, icono, descripcion in servicios:
            servicio_frame = tk.Frame(frame, bg="#f0f8ff")
            servicio_frame.pack(fill="x", padx=20, pady=5)
            
            tk.Radiobutton(servicio_frame, text=f"{icono} {servicio}",
                          variable=self.var_servicio, value=servicio,
                          font=("Arial", 10, "bold"),
                          bg="#f0f8ff").pack(anchor="w")
            
            tk.Label(servicio_frame, text=f"   {descripcion}",
                    font=("Arial", 8), bg="#f0f8ff",
                    fg="#6b7280").pack(anchor="w")
    
    def _crear_seccion_resultados(self, parent):
        """Sección para mostrar resultados"""
        frame = tk.LabelFrame(parent, text="📊 Resultados de Búsqueda",
                             font=("Arial", 11, "bold"),
                             bg="#f0f8ff", relief="groove", bd=2)
        frame.pack(fill="both", expand=True, pady=15)
        
        # Área de texto con scroll
        text_frame = tk.Frame(frame, bg="#f0f8ff")
        text_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.text_resultados = tk.Text(text_frame, height=15, font=("Consolas", 9),
                                      wrap=tk.WORD, relief="solid", bd=1)
        
        scroll_vertical = ttk.Scrollbar(text_frame, orient="vertical",
                                       command=self.text_resultados.yview)
        self.text_resultados.configure(yscrollcommand=scroll_vertical.set)
        
        self.text_resultados.pack(side="left", fill="both", expand=True)
        scroll_vertical.pack(side="right", fill="y")
        
        # Botón para ver mapa
        self.boton_mapa = tk.Button(frame, text="🗺️ VER MAPA INTERACTIVO",
                                   font=("Arial", 10, "bold"),
                                   bg="#7c3aed", fg="white",
                                   command=self.mostrar_mapa,
                                   state="disabled",
                                   relief="flat", bd=0, padx=20, pady=10)
        self.boton_mapa.pack(pady=10)
        
        # Mensaje inicial
        mensaje_inicial = """🚨 SISTEMA DE EMERGENCIAS - GUAYAQUIL
═══════════════════════════════════════════════════════════════════

🎓 PROYECTO ACADÉMICO - MATEMÁTICAS DISCRETAS
👥 Integrantes: Catalina Calderón, Fátima Torres, Jack Vera, Danna Zambrano
🏫 Docente: Cristhian Hernández Rodríguez

📊 ALGORITMO IMPLEMENTADO: Floyd-Warshall
🌐 FUENTE DE DATOS: OpenStreetMap (descarga automática)
🏙️ ÁREA DE COBERTURA: Guayaquil, Ecuador

✨ CARACTERÍSTICAS:
  ✅ Red vial real de Guayaquil
  ✅ Cálculo automático de rutas óptimas
  ✅ Integración de servicios de emergencia
  ✅ Visualización en mapas interactivos

📝 INSTRUCCIONES:
  1️⃣ Cargar red vial (puede tomar 1-2 minutos)
  2️⃣ Configurar su ubicación
  3️⃣ Seleccionar tipo de emergencia
  4️⃣ Buscar servicios cercanos
  5️⃣ Ver resultados y mapa interactivo

⚠️  En emergencias reales, marque 911 (ECU911)
"""
        self.text_resultados.insert(tk.END, mensaje_inicial)
    
    def _crear_seccion_info(self, parent):
        """Sección de información del proyecto"""
        frame = tk.Frame(parent, bg="#e0f2fe", relief="solid", bd=1)
        frame.pack(fill="x", pady=10)
        
        info_text = """ℹ️  INFORMACIÓN DEL PROYECTO
🎯 Objetivo: Optimizar rutas de servicios de emergencia usando Matemáticas Discretas
⚡ Algoritmo: Floyd-Warshall para encontrar caminos más cortos entre todos los pares de nodos
🌐 Datos: Red vial real obtenida automáticamente de OpenStreetMap
📍 Zona: Ceibos + ESPOL (bbox fija) • ~≤2000 nodos"""
        
        tk.Label(frame, text=info_text, font=("Arial", 8),
                bg="#e0f2fe", fg="#1e40af", justify="left").pack(pady=8, padx=15)
    
    def cargar_red(self, solo_ceibos=False):
        """Carga la red vial en un hilo separado"""
        def cargar_en_hilo():
            self.estado_red.config(text="🔄 Descargando red vial...", fg="#f59e0b")
            self.root.update()
            
            if self.sistema.cargar_red_vial(solo_ceibos):
                self.estado_red.config(text="🔄 Preparando Floyd-Warshall...", fg="#f59e0b")
                self.root.update()
                
                if self.sistema.preparar_floyd_warshall():
                    zona = "Ceibos" if solo_ceibos else "Guayaquil"
                    nodos = len(self.sistema.red_vial["nodos"])
                    self.estado_red.config(
                        text=f"✅ Red cargada: {zona} ({nodos:,} nodos)", 
                        fg="#059669"
                    )
                    messagebox.showinfo("Éxito", f"Red vial de {zona} cargada correctamente")
                else:
                    self.estado_red.config(text="❌ Error preparando algoritmo", fg="#dc2626")
            else:
                self.estado_red.config(text="❌ Error cargando red", fg="#dc2626")
        
        # Ejecutar en hilo separado para no bloquear la UI
        threading.Thread(target=cargar_en_hilo, daemon=True).start()
    
    def localizar_usuario(self):
        """Localiza la ubicación del usuario"""
        ubicacion_texto = self.var_ubicacion.get().strip()
        
        if not ubicacion_texto:
            messagebox.showerror("Error", "Por favor, ingrese una ubicación")
            return
        
        coordenadas = self.sistema.buscar_ubicacion(ubicacion_texto)
        
        if coordenadas:
            self.ubicacion_usuario = coordenadas
            self.nombre_ubicacion = ubicacion_texto
            self.estado_ubicacion.config(
                text=f"✅ Ubicado: {ubicacion_texto}\n📍 {coordenadas[0]:.4f}, {coordenadas[1]:.4f}",
                fg="#059669"
            )
            messagebox.showinfo("Éxito", f"Ubicación encontrada: {ubicacion_texto}")
        else:
            self.estado_ubicacion.config(text="❌ Ubicación no encontrada", fg="#dc2626")
            messagebox.showerror("Error", "Ubicación no encontrada. Intente con: ESPOL, Malecón 2000, Mall del Sol, etc.")
    
    def buscar_servicios(self):
        """Busca servicios de emergencia"""
        if not self.sistema.fw_preparado:
            messagebox.showerror("Error", "Primero debe cargar la red vial")
            return
        
        if not self.ubicacion_usuario:
            messagebox.showerror("Error", "Primero debe configurar su ubicación")
            return
        
        tipo_servicio = self.var_servicio.get()
        
        self.text_resultados.delete(1.0, tk.END)
        self.text_resultados.insert(tk.END, "🔄 Buscando servicios y calculando rutas óptimas...\n")
        self.root.update()
        
        try:
            resultados = self.sistema.encontrar_servicios_cercanos(
                self.ubicacion_usuario, tipo_servicio, max_resultados=5
            )
            
            if not resultados:
                messagebox.showinfo("Sin resultados", 
                                   f"No se encontraron servicios de {tipo_servicio} con rutas disponibles")
                return
            
            self.resultados_actuales = resultados
            self._mostrar_resultados(resultados, tipo_servicio)
            self.boton_mapa.config(state="normal")
            
        except Exception as e:
            messagebox.showerror("Error", f"Error en la búsqueda: {str(e)}")
    
    def _mostrar_resultados(self, resultados, tipo_servicio):
        """Muestra los resultados en el área de texto"""
        self.text_resultados.delete(1.0, tk.END)
        
        # Header
        header = f"""{'='*80}
🚨 SERVICIOS DE {tipo_servicio.upper()} MÁS CERCANOS - FLOYD-WARSHALL
{'='*80}

📍 Su ubicación: {self.nombre_ubicacion}
🎯 Servicios encontrados: {len(resultados)}
🕒 Búsqueda realizada: {time.strftime('%H:%M:%S')}
⚡ Algoritmo: Floyd-Warshall (camino más corto)

🏆 RANKING POR DISTANCIA Y TIEMPO
{'-'*80}
"""
        
        self.text_resultados.insert(tk.END, header)
        
        # Resultados individuales
        for i, resultado in enumerate(resultados, 1):
            servicio = resultado["servicio"]
            distancia = resultado["distancia_km"]
            tiempo = resultado["tiempo_min"]
            
            servicio_info = f"""
#{i} {servicio.nombre}
{'-' * (len(servicio.nombre) + 3)}
    📏 Distancia:         {distancia:.2f} km
    ⏱️ Tiempo estimado:   {tiempo:.1f} minutos
    🛣️ Tipo de ruta:      Red vial real (Floyd-Warshall)
    📞 Teléfono:          {servicio.telefono}
"""
            
            if servicio.especialidad:
                servicio_info += f"    🏥 Especialidad:      {servicio.especialidad}\n"
            if servicio.zona:
                servicio_info += f"    🗺️ Zona:             {servicio.zona}\n"
            
            if i == 1:
                servicio_info += "    💡 RECOMENDADO: Servicio más cercano\n"
            
            self.text_resultados.insert(tk.END, servicio_info)
        
        # Footer
        mejor = resultados[0]
        footer = f"""{'-'*80}
💡 RECOMENDACIÓN PRINCIPAL:
   {mejor['servicio'].nombre}
   📏 {mejor['distancia_km']:.2f} km - ⏱️ {mejor['tiempo_min']:.1f} min

🚨 NÚMEROS DE EMERGENCIA:
   • ECU 911: 911 (Emergencias generales)
   • Bomberos: 102
   • Policía: 101
   • Cruz Roja: 131

📱 INSTRUCCIONES:
   1. Haga clic en "VER MAPA INTERACTIVO" para navegación visual
   2. Las rutas calculadas siguen la red vial real de Guayaquil
   3. Los tiempos consideran velocidad urbana promedio (25 km/h)
   4. En emergencias críticas, llame primero al 911

🔬 ALGORITMO FLOYD-WARSHALL:
   ✅ Garantiza el camino más corto entre todos los puntos
   ✅ Procesa {len(self.sistema.red_vial['nodos']):,} nodos de la red real
   ✅ Optimización matemática basada en teoría de grafos

{'-'*80}
✅ Búsqueda completada. Mapa interactivo disponible.
"""
        
        self.text_resultados.insert(tk.END, footer)
    
    def mostrar_mapa(self):
        """Genera y muestra el mapa interactivo"""
        if not self.resultados_actuales:
            messagebox.showerror("Error", "No hay resultados para mostrar en el mapa")
            return
        
        try:
            self._generar_mapa_folium()
            
        except Exception as e:
            messagebox.showerror("Error", f"Error generando mapa: {str(e)}")
    
    def _generar_mapa_folium(self):
        """Genera mapa con Folium"""
        # Centro del mapa en Guayaquil
        centro_guayaquil = [-2.1709, -79.9218]
        
        mapa = folium.Map(
            location=centro_guayaquil,
            zoom_start=12,
            tiles="CartoDB positron"
        )
        
        # Marcador del usuario
        folium.Marker(
            location=list(self.ubicacion_usuario),
            popup=folium.Popup(
                f"<b>📍 Su Ubicación</b><br>"
                f"{self.nombre_ubicacion}<br>"
                f"<i>Lat: {self.ubicacion_usuario[0]:.5f}</i><br>"
                f"<i>Lon: {self.ubicacion_usuario[1]:.5f}</i>",
                max_width=300
            ),
            icon=folium.Icon(color="black", icon="home", prefix="fa")
        ).add_to(mapa)
        
        # Colores para diferentes tipos de servicios
        colores = {
            'Hospital': 'red',
            'Policía': 'blue', 
            'Bomberos': 'orange'
        }
        
        tipo_servicio = self.var_servicio.get()
        color_servicio = colores.get(tipo_servicio, 'purple')
        
        # Agregar servicios y rutas
        for i, resultado in enumerate(self.resultados_actuales):
            servicio = resultado["servicio"]
            coordenadas_ruta = resultado["ruta_coordenadas"]
            
            # Ruta
            if coordenadas_ruta:
                folium.PolyLine(
                    locations=[[lat, lon] for lon, lat in coordenadas_ruta],
                    weight=5 if i == 0 else 3,
                    color='purple' if i == 0 else color_servicio,
                    opacity=0.8,
                    tooltip=f"Ruta a {servicio.nombre}"
                ).add_to(mapa)
            
            # Marcador del servicio
            popup_texto = f"""<b>{servicio.nombre}</b><br>
📏 {resultado['distancia_km']:.2f} km<br>
⏱️ {resultado['tiempo_min']:.1f} min<br>
📞 {servicio.telefono}"""
            
            if servicio.especialidad:
                popup_texto += f"<br>🏥 {servicio.especialidad}"
            
            folium.Marker(
                location=list(servicio.coordenadas),
                popup=folium.Popup(popup_texto, max_width=300),
                icon=folium.Icon(
                    color='purple' if i == 0 else color_servicio,
                    icon='plus',
                    prefix='fa'
                )
            ).add_to(mapa)
        
        # Guardar y abrir mapa
        archivo_mapa = "mapa_emergencias_guayaquil.html"
        mapa.save(archivo_mapa)
        
        # Abrir en navegador
        ruta_completa = os.path.abspath(archivo_mapa)
        webbrowser.open(f"file://{ruta_completa}")
    
    def ejecutar(self):
        """Inicia la aplicación"""
        # Configurar eventos de teclado
        self.entry_ubicacion.bind("<Return>", lambda e: self.localizar_usuario())
        self.root.bind("<F5>", lambda e: self.buscar_servicios() if self.ubicacion_usuario and self.sistema.fw_preparado else None)
        
        self.root.mainloop()


def main():
    """Función principal"""
    print("🚨 Sistema de Rutas de Emergencia - Guayaquil")
    print("="*60)
    print("🎓 Proyecto: Matemáticas Discretas")
    print("⚡ Algoritmo: Floyd-Warshall")
    print("🌐 Datos: OpenStreetMap (descarga automática)")
    print("🏙️ Área: Guayaquil, Ecuador")
    print("="*60)
    
    try:
        app = InterfazSistemaEmergencia()
        app.ejecutar()
    except Exception as e:
        print(f"❌ Error iniciando aplicación: {e}")
        input("Presione Enter para salir...")

if __name__ == "__main__":
    main()