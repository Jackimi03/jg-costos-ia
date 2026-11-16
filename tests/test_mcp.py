import tempfile
import unittest
from pathlib import Path
from jg.mcp_server import build_server

class McpTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_tool_dispatch_does_not_apply_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            server=build_server(Path(temp)/'mcp.db')
            names={x.name for x in await server.list_tools()}
            self.assertIn('consultar_precios',names)
            self.assertIn('preparar_presupuesto',names)
            result=await server.call_tool('preparar_presupuesto',{'version':0,'partidas':[{'row':10,'quantity':'70','unit_price':'7'}]})
            # Structured tool results include the exact proposal for user review.
            self.assertIn('490.00',str(result))
            current=await server.call_tool('consultar_borrador',{})
            self.assertIn('0.00',str(current))

if __name__=='__main__':unittest.main()
