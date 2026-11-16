(function(root){
  'use strict';
  function lineCents(quantity,price){
    if(!/^\d+(?:\.\d{1,6})?$/.test(quantity)||!/^\d+(?:\.\d{1,2})?$/.test(price))throw Error('Use números decimales positivos.');
    const parts=quantity.split('.'),p=price.split('.');
    const q=BigInt(parts[0])*1000000n+BigInt((parts[1]||'').padEnd(6,'0'));
    const cents=BigInt(p[0])*100n+BigInt((p[1]||'').padEnd(2,'0'));
    if(q<=0n||q>1000000000000n||cents<=0n||cents>100000000n)throw Error('Cantidad o precio fuera de rango.');
    const subtotal=(q*cents+500000n)/1000000n;
    if(subtotal>100000000000n)throw Error('Importe fuera de rango.');
    return subtotal;
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={lineCents};else root.JGMath={lineCents};
})(typeof globalThis!=='undefined'?globalThis:this);
