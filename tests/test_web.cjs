const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const math=require('../jg/web_math.js');
assert.equal(math.lineCents('0.58','10.50'),609n);
assert.equal(math.lineCents('70','7'),49000n);
assert.throws(()=>math.lineCents('-1','7'));
assert.throws(()=>math.lineCents('1','NaN'));
class Element{
 constructor(){this.textContent='';this.value='';this.children=[];this.style={};this.disabled=false;this.isConnected=true;this.classList={toggle(){},add(){},remove(){}};}
 append(...x){this.children.push(...x);if(this===els.service&&!this.value&&x[0])this.value=String(x[0].value);}
 replaceChildren(...x){this.children=[...x];this.value='';}
 setAttribute(k,v){this[k]=v;}
}
const els={},all=[];
const doc={getElementById:id=>els[id]||(els[id]=new Element()),createElement:tag=>{const e=new Element();e.tag=tag;all.push(e);return e;},querySelectorAll:()=>Object.values(els).concat(all)};
const initial={version:0,status:'draft',lines:[],total:'0.00'};
const data={'/session':{token:'test'},'/workspace':{projects:[{id:'personal',name:'Actual'},{id:'other',name:'Otro'}]},'/projects/personal/budget':initial,'/projects/personal/catalog':[{row:8,name:'Impermeabilización',min:'10.50',max:null,kind:'from',active:true,unit:'m²'}],'/projects/personal/history':[]};
const ctx={document:doc,window:{addEventListener(){},print(){}},Intl,console,setTimeout,clearTimeout,confirm:()=>true,fetch:async path=>({ok:true,json:async()=>data[path]}),JGMath:math};
vm.createContext(ctx);const html=fs.readFileSync('jg/web.html','utf8');for(const m of html.matchAll(/id="([^"]+)"/g))doc.getElementById(m[1]);const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
vm.runInContext(script,ctx);
(async()=>{
 await new Promise(r=>setTimeout(r,30));
 ctx.api=async()=>{throw Error('Sin conexión');};
 const other=els.projects.children.flatMap(x=>x.children.length?x.children:[x]).find(x=>x.textContent==='Otro');assert.ok(other);
 await other.onclick();
 assert.equal(vm.runInContext('project',ctx),'personal','Una carga fallida no cambia proyecto');
 assert.equal(els.title.textContent,'Actual');
 let release;const pending=new Promise(r=>release=r);const running=ctx.run(()=>pending);
 assert.equal(els['new-name'].disabled,true,'Los campos no se editan durante la petición');
 release();await running;
 assert.equal(els['new-name'].disabled,false);
 vm.runInContext("budget={version:1,status:'draft',total:'6.09',lines:[]};lines=[{row:8,name:'Impermeabilización',unit:'m²',quantity:'0.58',unit_price:'10.50',subtotal:'6.09'}];dirty=false;draw()",ctx);
 assert.match(els.total.textContent,/6\.09/);
 await vm.runInContext('actions.print()',ctx);
 assert.match(els['print-state'].textContent,/BORRADOR SIN APROBACIÓN.*1/);
 assert.equal(els['print-project'].textContent,'Actual');
 vm.runInContext("lines=[{row:8,name:'Impermeabilización',unit:'m²',quantity:'',unit_price:''}];draw()",ctx);
 const inputs=els.lines.children[0].children.slice(1,3).map(cell=>cell.children[0]);
 assert.equal(typeof inputs[0].oninput,'function','Cada pulsación actualiza el modelo sin destruir el campo');
 inputs[0].value='0.58';inputs[0].oninput();inputs[1].value='10.50';inputs[1].oninput();
 assert.match(els.total.textContent,/6\.09/);
 await vm.runInContext('lines=[];dirty=false;actions.add()',ctx);
 assert.equal(vm.runInContext('lines[0].unit_price',ctx),'10.50','La tarifa JG se carga automáticamente');
 ctx.api=async()=>({state:'done'});
 vm.runInContext("activeJob={id:'ai',project:'personal'};proposal={id:'preview'}",ctx);
 await els['ai-stop'].onclick();
 assert.equal(vm.runInContext('proposal',ctx),null,'Cancelar descarta la propuesta incluso si la IA acaba al mismo tiempo');
 let writes=0;ctx.api=async()=>{writes++;return {};};
 const trash=els.projects.children[0].children[1];trash.onclick();
 assert.equal(writes,0,'Abrir confirmación de papelera no borra');
 assert.equal(vm.runInContext('pendingTrash.id',ctx),'personal');
 els['trash-confirm-no'].onclick();
 assert.equal(vm.runInContext('pendingTrash',ctx),null,'Conservar cancela el borrado');
 console.log('Interfaz: centavos exactos, proyecto intacto al fallar carga y campos bloqueados durante peticiones.');
})().catch(e=>{console.error(e);process.exitCode=1;});
