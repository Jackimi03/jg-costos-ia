"""Owner-only MCP over stdio, suitable for local launch or an authenticated SSH command."""
import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from .api import Line
from .workspace import setup,OWNER,projects,create_project,sheet_link
from .lifecycle import list_trash,trash_project,restore_project,prepare_undo
from .takeoff import calculate_takeoff

def build_server(path):
    store=setup(path)
    mcp=FastMCP('JG Costos IA',instructions='Sistema privado del propietario. Consulte precios propios; materiales aparte. Nunca invente precios. Muestre la propuesta completa y obtenga confirmación explícita antes de confirmar_presupuesto. No apruebe comercialmente ni envíe cotizaciones por MCP. Google Sheets requiere su conector autorizado; exportar_para_sheets solo prepara valores, no escribe en Google.')
    read=ToolAnnotations(readOnlyHint=True,openWorldHint=False)
    write=ToolAnnotations(readOnlyHint=False,destructiveHint=False,openWorldHint=False)
    @mcp.tool(annotations=read)
    def consultar_proyectos()->dict:
        """Lista presupuestos privados y el enlace de Google Sheets, si está configurado."""
        return {'projects':projects(store),'trash':list_trash(store),'sheet_url':sheet_link(store)}
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=True,openWorldHint=False))
    def enviar_a_papelera(proyecto:str)->dict:
        """Solo cuando el usuario pide borrar este presupuesto. Conserva datos e historial; se puede restaurar."""
        return trash_project(store,proyecto)
    @mcp.tool(annotations=write)
    def restaurar_presupuesto(proyecto:str)->dict:
        """Restaura de la papelera el presupuesto elegido por el usuario."""
        return restore_project(store,proyecto)
    @mcp.tool(annotations=write)
    def preparar_deshacer(proyecto:str='personal')->dict:
        """Muestra antes/después de deshacer el último guardado. Requiere confirmar_presupuesto para aplicar."""
        return prepare_undo(store,proyecto)
    @mcp.tool(annotations=read)
    def calcular_cantidades_materiales(cantidad_actividad:str,recursos:list[dict])->dict:
        """Calcula cantidades y compras con coeficientes explícitos, desperdicio y envases. No inventa rendimientos ni precios; todo recurso se marca sin verificar."""
        return calculate_takeoff(cantidad_actividad,recursos)
    @mcp.tool(annotations=write)
    def crear_presupuesto(nombre:str)->dict:
        """Crea un presupuesto vacío independiente; no sobrescribe otro."""
        return create_project(store,nombre)
    @mcp.tool(annotations=read)
    def consultar_precios(proyecto:str='personal')->list[dict]:
        """Referencias JG en USD de mano de obra, con mínimos/rangos, exclusiones y procedencia."""
        return store.catalog(OWNER,proyecto)
    @mcp.tool(annotations=read)
    def consultar_borrador(proyecto:str='personal')->dict:
        """Consulta la versión actual, sin escribir ni aprobar."""
        return store.budget(OWNER,proyecto)
    @mcp.tool(annotations=write)
    def preparar_presupuesto(version:int,partidas:list[Line],proyecto:str='personal')->dict:
        """Prepara reemplazo COMPLETO de partidas. Precio y cantidad son explícitos; preserve partidas existentes salvo petición de quitarlas. Devuelve antes/después y hash; no aplica ni aprueba."""
        return store.preview(OWNER,proyecto,version,[p.model_dump() for p in partidas])
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=True,idempotentHint=True,openWorldHint=False))
    def confirmar_presupuesto(propuesta_id:str,hash_confirmado:str,proyecto:str='personal')->dict:
        """Solo tras confirmación explícita del usuario sobre esta propuesta exacta. Guarda el borrador versionado; no aprueba comercialmente ni escribe en Google."""
        return store.apply(OWNER,proyecto,propuesta_id,hash_confirmado)
    @mcp.tool(annotations=read)
    def exportar_para_sheets(proyecto:str='personal')->dict:
        """Prepara valores para revisión antes de copiar por el conector de Google. No sincroniza automáticamente ni elimina datos."""
        b=store.budget(OWNER,proyecto)
        values=[['Servicio','Unidad','Cantidad','Precio USD','Importe USD','Fuente']]
        values.extend([[r['name'],r['unit'],r['quantity'],r['unit_price'],r['subtotal'],r['source_cell']] for r in b['lines']])
        values.append(['TOTAL','','','',b['total'],''])
        return {'sheet_url':sheet_link(store),'budget_version':b['version'],'status':b['status'],'values':values,'requires_user_preview':True,'notice':'Use una pestaña de copia dedicada; no reemplace el catálogo original.'}
    return mcp

if __name__=='__main__':
    db=os.getenv('JG_DATABASE',str(Path(__file__).parent.parent/'data/personal.db'))
    build_server(db).run(transport='stdio')
