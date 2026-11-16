const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const source=fs.readFileSync('apps-script/Code.gs','utf8');
let values=[],formulas=[],properties={},userProperties={},writes=0;
const sheet={getRange:()=>({getValues:()=>values,getFormulas:()=>formulas,setValues:x=>{values=x;writes++;return {setNumberFormat:()=>{}}}}),getMaxRows:()=>210};
const ctx={console,JSON,Error,encodeURIComponent,
 PropertiesService:{getScriptProperties:()=>({getProperty:k=>properties[k]}),getUserProperties:()=>({getProperty:k=>userProperties[k],setProperty:(k,v)=>userProperties[k]=v,deleteProperty:k=>delete userProperties[k]})},
 Utilities:{DigestAlgorithm:{SHA_256:'sha256'},Charset:{UTF_8:'utf8'},computeDigest:(alg,s)=>crypto.createHash(alg).update(s).digest(),base64EncodeWebSafe:x=>Buffer.from(x).toString('base64url')},
 SpreadsheetApp:{getActive:()=>({getId:()=> 'sheet-demo',getSheetByName:()=>sheet,insertSheet:()=>{throw Error('unexpected')}})},
 LockService:{getDocumentLock:()=>({waitLock:()=>{},releaseLock:()=>{}})}};
vm.createContext(ctx);vm.runInContext(source,ctx);
properties.JG_SHEET_ID='other';
assert.throws(()=>ctx.jgRender_({version:1,lines:[]}),/prueba/);
properties.JG_SHEET_ID='sheet-demo';
ctx.jgRender_({version:1,status:'draft',total:'10.00',lines:[{name:'=IMPORTXML("x")',unit:'m²',quantity:'2',unit_price:'5.00',subtotal:'10.00',source_cell:'A8:E8'}]});
assert.equal(writes,1);
assert.equal(values[0][0],'JG Costos IA · Borrador');
assert.equal(values[4][0],'\'=IMPORTXML("x")');
assert.equal(values[4][3],5);
const previous=ctx.jgFingerprint_();formulas=[['=1+1']];
assert.notEqual(ctx.jgFingerprint_(),previous);
properties.JG_API_URL='https://example.invalid';properties.JG_PROJECT='demo';
ctx.jgApi_=()=>({id:'proposal',hash:'confirmed',before:{huge:'x'.repeat(20000)},after:{huge:'x'.repeat(20000)}});
ctx.jgPreview({});assert.ok(userProperties.JG_PENDING.length<300);
const beforeWrites=writes;
ctx.jgApi_=()=>{values=[['manual edit']];return {version:2,lines:[]};};
assert.throws(()=>ctx.jgApply('proposal','confirmed'),/hoja cambió durante/);
assert.equal(writes,beforeWrites);
console.log('Apps Script: destino permitido, fórmulas neutralizadas, cambios de fórmula detectados, estado compacto y edición concurrente preservada.');
