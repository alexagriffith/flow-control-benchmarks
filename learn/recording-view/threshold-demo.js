/* Recording-only motion; all plots, data, labels and geometry come from the learner. */
(() => {
  const $=id=>document.getElementById(id), canvas=$('stage'),ctx=canvas.getContext('2d');
  const duration=8000, ns='http://www.w3.org/2000/svg';
  const clamp=x=>Math.max(0,Math.min(1,x)),smooth=x=>{x=clamp(x);return x*x*(3-2*x)};
  let raf, origin, layers, frameUrl;
  // Same coordinate system as kneeEnsure, without its page-fit transform.
  const bounds={x:110,y:45,w:790,h:444.375};
  const image=async svg=>{const url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)],{type:'image/svg+xml'}));const img=new Image();img.src=url;await img.decode();URL.revokeObjectURL(url);return img};
  function drawLayer(name,alpha=1,maxX=Infinity){
    if(alpha<=0)return;ctx.save();ctx.globalAlpha=alpha;
    if(Number.isFinite(maxX)){ctx.beginPath();ctx.rect(0,0,(maxX-bounds.x)*1440/bounds.w,810);ctx.clip()}
    ctx.drawImage(layers[name],0,0,1440,810);ctx.restore();
  }
  function render(t){
    ctx.fillStyle='#0e1014';ctx.fillRect(0,0,1440,810);drawLayer('base');
    // Reveal both measured series at the same concurrency, never interpolate new values.
    const measured=clamp((t-250)/3000), schematic=clamp((t-3600)/2700);
    drawLayer('measured',1,measured===1?Infinity:172+measured*288);
    drawLayer('leftCandidate',smooth((t-3200)/500));
    drawLayer('schematic',1,schematic===1?Infinity:600.4+schematic*(801.28-600.4));
    drawLayer('rightCandidate',smooth((t-6400)/600));
    $('seek').value=String(t);canvas.dataset.time=String(t);
  }
  $('source').addEventListener('load',async()=>{
    try{
      const doc=$('source').contentDocument;
      doc.documentElement.dataset.theme='dark';
      // The hidden iframe can throttle animation, but kneeEnsure's SVG is synchronous.
      if(!doc.querySelector('#kneeThroughput'))throw Error('Source knee chart is unavailable');
      const original=doc.querySelector('#kneeScene'), scene=original.cloneNode(true);
      scene.removeAttribute('transform');
      const originals=[original,...original.querySelectorAll('*')], clones=[scene,...scene.querySelectorAll('*')];
      const props=['fill','stroke','stroke-width','stroke-linecap','stroke-linejoin','font-family','font-size','font-weight','text-anchor','letter-spacing'];
      originals.forEach((el,i)=>{const css=doc.defaultView.getComputedStyle(el);clones[i].removeAttribute('style');for(const p of props)clones[i].style.setProperty(p,css.getPropertyValue(p));clones[i].style.strokeDasharray=el.getAttribute('stroke-dasharray')||'none';clones[i].style.strokeDashoffset='0'});
      // Retain the accepted recording title; current learner has a more specific title.
      scene.querySelector('text').textContent='Where control starts';
      const viz=scene.querySelector('#kneeViz');
      for(const el of viz.children){
        const id=el.id, x=Number(el.getAttribute('x')??el.getAttribute('x1'));
        let layer='base';
        if(['kneeThroughput','kneeLatency'].includes(id)||(el.tagName==='circle'&&id!=='kneeDot'))layer='measured';
        if(['kneeFlat','kneeQueue'].includes(id))layer='schematic';
        if(['kneeDot','kneeValueLabel'].includes(id)||el.textContent==='candidate · 128'||((el.tagName==='rect'||el.tagName==='line')&&Math.abs(x-336.57142857)<.01))layer='leftCandidate';
        if(['kneeSchematicThreshold','kneeFlatLabel','kneeClimbLabel'].includes(id)||el.textContent==='candidate region'||(el.tagName==='rect'&&Math.abs(x-731.84)<.01))layer='rightCandidate';
        el.dataset.layer=layer;
      }
      layers={};
      for(const layer of ['base','measured','schematic','leftCandidate','rightCandidate']){
        const svg=doc.createElementNS(ns,'svg');svg.setAttribute('xmlns',ns);svg.setAttribute('viewBox',`${bounds.x} ${bounds.y} ${bounds.w} ${bounds.h}`);svg.setAttribute('width','1440');svg.setAttribute('height','810');
        const copy=scene.cloneNode(true);for(const el of copy.querySelectorAll('[data-layer]'))if(el.dataset.layer!==layer)el.remove();
        if(layer!=='base')for(const child of [...copy.children])if(child.id!=='kneeViz')child.remove();
        svg.append(copy);layers[layer]=await image(svg);
      }
      const fingerprints=['kneeThroughput','kneeLatency','kneeFlat','kneeQueue'].map(id=>({id,d:doc.getElementById(id).getAttribute('d')}));
      canvas.dataset.sourcePaths=JSON.stringify(fingerprints);
      $('play').onclick=()=>{cancelAnimationFrame(raf);origin=performance.now();const tick=now=>{const t=Math.min(duration,now-origin);render(t);if(t<duration)raf=requestAnimationFrame(tick)};raf=requestAnimationFrame(tick)};
      $('seek').oninput=()=>{cancelAnimationFrame(raf);render(Number($('seek').value))};
      $('export').onclick=async()=>{
        cancelAnimationFrame(raf);$('play').disabled=$('export').disabled=true;
        try{
          const parts=[],enc=new TextEncoder(),field=(h,o,n,v)=>h.set(enc.encode(v).subarray(0,n),o);
          for(let i=0;i<480;i++){
            render(i*1000/60);const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.98));if(!blob)throw Error('Frame export failed');
            const h=new Uint8Array(512);field(h,0,100,`frame-${String(i).padStart(5,'0')}.jpg`);field(h,100,8,'0000644\0');field(h,108,8,'0000000\0');field(h,116,8,'0000000\0');field(h,124,12,blob.size.toString(8).padStart(11,'0')+'\0');field(h,136,12,'00000000000\0');h.fill(32,148,156);h[156]=48;field(h,257,6,'ustar\0');field(h,263,2,'00');field(h,148,8,h.reduce((a,b)=>a+b,0).toString(8).padStart(6,'0')+'\0 ');parts.push(h,blob,new Uint8Array((512-blob.size%512)%512));
            if(i%30===0){$('status').textContent=`Exporting ${i+1}/480`;await new Promise(requestAnimationFrame)}
          }
          parts.push(new Uint8Array(1024));if(frameUrl)URL.revokeObjectURL(frameUrl);frameUrl=URL.createObjectURL(new Blob(parts,{type:'application/x-tar'}));$('download').href=frameUrl;$('download').download='chapter-3-threshold-motion-frames.tar';$('download').hidden=false;$('status').textContent='480 frames · 60 fps';
        }catch(e){$('status').textContent=e.message}finally{$('play').disabled=$('export').disabled=false}
      };
      render(0);$('play').disabled=$('export').disabled=false;$('status').textContent='Ready · 8 seconds';document.documentElement.dataset.ready='true';
    }catch(e){$('status').textContent=e.message;document.documentElement.dataset.error=e.message}
  });
})();
