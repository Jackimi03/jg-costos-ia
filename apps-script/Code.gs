function onOpen() {
  SpreadsheetApp.getUi().createMenu('JG Costos IA').addItem('Abrir asistente', 'jgOpen').addToUi();
}
function jgOpen() {
  SpreadsheetApp.getUi().showSidebar(HtmlService.createHtmlOutputFromFile('Panel').setTitle('JG Costos IA'));
}
function jgConfig_() {
  const p=PropertiesService.getScriptProperties();
  const url=p.getProperty('JG_API_URL'), project=p.getProperty('JG_PROJECT');
  if (!url || !/^https:\/\//.test(url) || !project) throw new Error('Falta configurar API HTTPS y proyecto de prueba.');
  if (p.getProperty('JG_SHEET_ID')!==SpreadsheetApp.getActive().getId()) throw new Error('Solo se permite la hoja de prueba configurada.');
  return {url:url.replace(/\/$/,''),project:encodeURIComponent(project)};
}
function jgApi_(method,path,body) {
  const c=jgConfig_(),token=ScriptApp.getIdentityToken();
  if (!token) throw new Error('Autorice su cuenta Google para continuar.');
  const options={method:method,headers:{Authorization:'Bearer '+token},muteHttpExceptions:true};
  if(body!==undefined){options.contentType='application/json';options.payload=JSON.stringify(body);}
  const r=UrlFetchApp.fetch(c.url+'/projects/'+c.project+path,options);
  const data=JSON.parse(r.getContentText());
  if(r.getResponseCode()>=300) throw new Error(typeof data.detail==='string'?data.detail:'Solicitud inválida. Revise los datos.');
  return data;
}
function jgLoad() {
  return {budget:jgApi_('get','/budget'),catalog:jgApi_('get','/catalog')};
}
function jgFingerprint_() {
  const sheet=SpreadsheetApp.getActive().getSheetByName('JG_Borrador');
  const range=sheet?sheet.getRange(1,1,210,6):null;
  const value=JSON.stringify(range?{values:range.getValues(),formulas:range.getFormulas()}:null);
  return Utilities.base64EncodeWebSafe(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,value,Utilities.Charset.UTF_8));
}
function jgPreview(body) {
  const p=jgApi_('post','/preview',body);
  PropertiesService.getUserProperties().setProperty('JG_PENDING',JSON.stringify({id:p.id,hash:p.hash,fingerprint:jgFingerprint_()}));
  return p;
}
function jgApply(id,hash) {
  jgConfig_();
  const lock=LockService.getDocumentLock();lock.waitLock(10000);
  try {
    const props=PropertiesService.getUserProperties(),raw=props.getProperty('JG_PENDING');
    if(!raw) throw new Error('Prepare una nueva vista previa.');
    const pending=JSON.parse(raw);
    if(pending.id!==id || pending.hash!==hash) throw new Error('La propuesta no coincide.');
    if(pending.fingerprint!==jgFingerprint_()) throw new Error('La hoja cambió. Prepare otra vista previa.');
    const budget=jgApi_('post','/apply',{id:id,hash:hash});
    if(pending.fingerprint!==jgFingerprint_()) throw new Error('El servidor guardó el borrador, pero la hoja cambió durante el guardado. No se sobrescribió: revise y recupere la copia del servidor cuando corresponda.');
    const latest=jgApi_('get','/budget');
    if(JSON.stringify(latest)!==JSON.stringify(budget)) throw new Error('Hay una versión más reciente. Vuelva a cargar antes de recuperar la copia.');
    if(pending.fingerprint!==jgFingerprint_()) throw new Error('La hoja cambió. No se sobrescribió; revise antes de recuperar la copia.');
    try { jgRender_(budget); }
    catch(e){ throw new Error('El borrador se guardó en el servidor, pero la hoja no se actualizó. No repita cambios: use Recuperar copia del servidor.'); }
    props.deleteProperty('JG_PENDING');
    return budget;
  } finally {lock.releaseLock();}
}
function jgRecover() {
  const ui=SpreadsheetApp.getUi();
  if(ui.alert('Recuperar copia','Se reemplazará únicamente JG_Borrador con la versión actual del servidor. ¿Continuar?',ui.ButtonSet.YES_NO)!==ui.Button.YES) return null;
  const lock=LockService.getDocumentLock();lock.waitLock(10000);
  try {const b=jgApi_('get','/budget');jgRender_(b);return b;} finally{lock.releaseLock();}
}
function jgSuggest(prompt) {return jgApi_('post','/suggest',{prompt:prompt});}
function jgSafe_(value) {return /^[=+\-@\t\r]/.test(String(value))?"'"+value:String(value);}
function jgRender_(budget) {
  const ss=SpreadsheetApp.getActive();
  if(PropertiesService.getScriptProperties().getProperty('JG_SHEET_ID')!==ss.getId()) throw new Error('Solo se permite la hoja de prueba configurada.');
  if(budget.lines.length>200) throw new Error('Demasiadas partidas.');
  const name='JG_Borrador',sheet=ss.getSheetByName(name)||ss.insertSheet(name);
  const rows=Array.from({length:210},()=>Array(6).fill(''));
  rows[0]=['JG Costos IA · Borrador','','','Versión',budget.version,''];
  rows[1]=['Mano de obra · USD · materiales aparte','','','Estado',budget.status,''];
  rows[3]=['Servicio','Unidad','Cantidad','Precio USD','Importe USD','Fuente'];
  budget.lines.forEach((x,i)=>{rows[i+4]=[jgSafe_(x.name),jgSafe_(x.unit),Number(x.quantity),Number(x.unit_price),Number(x.subtotal),jgSafe_(x.source_cell)];});
  rows[205]=['Total','','','',Number(budget.total),''];
  rows[207]=['Copia del servidor. Cambios manuales no se sincronizan.','','','','',''];
  sheet.getRange(1,1,210,6).setValues(rows);
  if(sheet.setFrozenRows) {
    sheet.setFrozenRows(4);sheet.setColumnWidth(1,300);sheet.setColumnWidths(2,5,120);
    sheet.getRange(1,1,2,6).setBackground('#172638').setFontColor('#FFFFFF');
    sheet.getRange(4,1,1,6).setBackground('#F4F0E7').setFontWeight('bold');
    sheet.getRange(5,3,201,1).setNumberFormat('#,##0.######');
    sheet.getRange(5,4,201,2).setNumberFormat('#,##0.00');
    sheet.getRange(206,1,1,6).setBackground('#F4F0E7').setFontWeight('bold');
  }
}
