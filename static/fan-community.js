(() => {
  'use strict';
  const api = location.hostname === 'issho-jleague.pages.dev' ? 'https://issho-jleague.onrender.com' : location.origin;
  const clubMatch = location.pathname.match(/^\/club\/([a-z0-9-]+)\/?$/);
  const club = clubMatch?.[1];
  const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
  async function call(path, body) {
    const response = await fetch(api + '/api/fan/' + path, {method: body === undefined ? 'GET' : 'POST', headers: body === undefined ? {} : {'Content-Type':'application/json'}, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(75000), credentials:'omit'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '通信できませんでした。時間をおいてお試しください。');
    return data;
  }
  function trafficSource() {
    const key='jleague-entry-source-v1';
    try { const saved=sessionStorage.getItem(key); if(saved) return saved; } catch (_) {}
    let source='direct';
    try {
      if(document.referrer){
        const host=new URL(document.referrer).hostname.toLowerCase();
        if(host===location.hostname||host==='issho-jleague.onrender.com') source='internal';
        else if(host==='t.co'||host==='x.com'||host.endsWith('.x.com')||host==='twitter.com'||host.endsWith('.twitter.com')) source='x';
        else if(host==='threads.net'||host.endsWith('.threads.net')||host.includes('instagram.com')||host.includes('facebook.com')) source='threads_sns';
        else if(host.includes('google.')) source='google_organic';
        else if(host.includes('bing.com')) source='bing_organic';
        else if(host.includes('search.yahoo.')) source='yahoo_organic';
        else source='referral';
      }
    } catch (_) { source='unknown'; }
    try { sessionStorage.setItem(key,source); } catch (_) {}
    return source;
  }
  // No tracking during previews/tests. The HTML never waits for the API to start.
  if (club && location.hostname === 'issho-jleague.pages.dev' && !navigator.webdriver && !new URLSearchParams(location.search).has('v')) {
    let timer;
    function scheduleView() {
      clearTimeout(timer);
      if (document.visibilityState !== 'visible') return;
      timer = setTimeout(() => {
        const key = 'fan-view:' + club;
        try { if (Date.now() - Number(sessionStorage.getItem(key) || 0) < 1800000) return; } catch (_) {}
        fetch(api + '/api/fan/view', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({club,source:trafficSource()}),credentials:'omit',keepalive:true}).then(response=>{
          if(response.ok) try { sessionStorage.setItem(key,String(Date.now())); } catch (_) {}
        }).catch(()=>{});
      },5000);
    }
    document.addEventListener('visibilitychange',scheduleView);scheduleView();
  }
  document.querySelectorAll('[data-fan-ranking]').forEach(section => {
    let data, expanded = false, league = section.dataset.defaultLeague || 'J1';
    const tabs = [...section.querySelectorAll('[data-rank-league]')], list = section.querySelector('.fan-rank-list'), more = section.querySelector('[data-rank-more]'), status = section.querySelector('[data-rank-status]');
    function paint() {
      tabs.forEach(t=>{const selected=t.dataset.rankLeague===league;t.setAttribute('aria-selected',String(selected));t.tabIndex=selected?0:-1;});
      list.setAttribute('aria-labelledby','fan-tab-'+league);list.replaceChildren();
      if (!data) return;
      const rows = data.leagues[league] || [];
      for (const row of expanded ? rows : rows.slice(0,5)) {
        const li=node('li',undefined,row.slug===club?'current':'');const a=node('a');a.href='/club/'+row.slug+'/';
        const rank=node('b',row.rank ? row.rank+'位':'—');if(row.tied)rank.append(node('small','同率'));if(!row.rank)rank.append(node('small','集計中'));
        a.append(rank,node('span',row.name));if(row.slug===club)a.append(node('small','このクラブ'));li.append(a);list.append(li);
      }
      const ranked=rows.some(r=>r.rank);status.textContent=(ranked?'過去7日間の閲覧順位':'計測を始めました。閲覧データが集まるまで順位は表示しません。')+' · '+data.updated_at+' JST更新';
      more.hidden=rows.length<=5;more.textContent=expanded?'上位5クラブに戻す':'全20クラブを見る';
      const mine=Object.entries(data.leagues).flatMap(([l,rs])=>rs.map(r=>({...r,league:l}))).find(r=>r.slug===club);
      section.querySelector('[data-rank-personal]').textContent=mine?(mine.rank?`このクラブは ${mine.league} ${mine.tied?'同率':''}${mine.rank}位`:'このクラブの順位は集計中'):'';
    }
    tabs.forEach((t,i)=>{t.addEventListener('click',()=>{league=t.dataset.rankLeague;expanded=false;paint();});t.addEventListener('keydown',event=>{let index;if(event.key==='ArrowRight')index=(i+1)%tabs.length;if(event.key==='ArrowLeft')index=(i+tabs.length-1)%tabs.length;if(event.key==='Home')index=0;if(event.key==='End')index=tabs.length-1;if(index!==undefined){event.preventDefault();tabs[index].click();tabs[index].focus();}});});
    more.addEventListener('click',()=>{expanded=!expanded;paint();});paint();
    fetch('/fan-rankings.json',{cache:'no-cache'}).then(r=>{if(!r.ok)throw Error();return r.json();}).then(d=>{data=d;paint();}).catch(()=>{status.textContent='ランキングを取得できませんでした。時間をおいて再表示してください。';});
  });
  const section=document.querySelector('[data-fan-messages]');if(!section)return;
  const form=section.querySelector('form'),status=section.querySelector('#cheer-status'),list=section.querySelector('[data-cheer-list]'),listStatus=section.querySelector('[data-cheer-list-status]');
  const messageClub=section.dataset.fanMessages;
  const ownKey=messageClub+'-own-cheers-v1';let own=[];try {own=JSON.parse(localStorage.getItem(ownKey)||'[]');if(!Array.isArray(own))own=[];} catch (_) {}
  function storeOwn(){try{localStorage.setItem(ownKey,JSON.stringify(own));}catch(_){status.textContent+=' このブラウザでは取り消し情報を保存できません。';}}
  function paintOwn(){const box=section.querySelector('[data-own-cheers]');box.replaceChildren();own.forEach(item=>{const row=node('div','自分の投稿：'+item.body,'fan-own-message');const b=node('button','自分の投稿を取り消す','fan-secondary');b.type='button';b.addEventListener('click',async()=>{b.disabled=true;try{await call('messages/'+item.id+'/delete',{delete_token:item.delete_token});own=own.filter(x=>x.id!==item.id);storeOwn();paintOwn();status.textContent='投稿を取り消しました。';await refresh();}catch(e){status.textContent=e.message;b.disabled=false;}});row.append(b);box.append(row);});}
  const dialog=node('dialog',undefined,'fan-report-dialog');dialog.setAttribute('aria-labelledby','fan-report-title');dialog.append(node('h3','メッセージを通報'));dialog.querySelector('h3').id='fan-report-title';const select=node('select');select.setAttribute('aria-label','通報理由');[['abuse','誹謗中傷・攻撃的な内容'],['personal','個人情報・権利侵害'],['spam','宣伝・迷惑投稿'],['other','その他の問題']].forEach(([value,label])=>{const o=node('option',label);o.value=value;select.append(o);});const send=node('button','通報する','fan-primary'),cancel=node('button','閉じる','fan-secondary'),reportStatus=node('p');reportStatus.setAttribute('role','status');dialog.append(select,send,cancel,reportStatus);document.body.append(dialog);let reportId;
  cancel.addEventListener('click',()=>dialog.close());send.addEventListener('click',async()=>{send.disabled=true;try{await call('messages/'+reportId+'/report',{reason:select.value});dialog.close();await refresh();listStatus.textContent='通報を受け付け、一時非表示にしました。';}catch(e){reportStatus.textContent=e.message;}finally{send.disabled=false;}});
  async function refresh(){listStatus.textContent='応援を読み込んでいます。初回は少し時間がかかる場合があります。';try{const d=await call('messages/'+messageClub);list.replaceChildren();d.messages.forEach(m=>{const card=node('article',undefined,'fan-message');const footer=node('footer');footer.append(node('span',m.nickname),node('time',new Date(m.created_at*1000).toLocaleDateString('ja-JP',{timeZone:'Asia/Tokyo'})));const report=node('button','通報');report.type='button';report.addEventListener('click',()=>{reportId=m.id;reportStatus.textContent='';dialog.showModal();});footer.append(report);card.append(node('p',m.body),footer);list.append(card);});listStatus.textContent=d.messages.length?'新しい応援から表示しています。':'最初の応援を待っています。確認済みの投稿がここに並びます。';}catch(e){listStatus.textContent='応援を読み込めませんでした。「更新」で再試行できます。';}}
  const length=value=>[...value.trim().normalize('NFC')].length;
  form.elements.body.addEventListener('input',()=>{const n=length(form.elements.body.value);section.querySelector('[data-cheer-count]').textContent=n+' / 20';form.elements.body.setCustomValidity(n>20?'20文字以内で入力してください。':'');});
  form.elements.nickname.addEventListener('input',()=>form.elements.nickname.setCustomValidity(length(form.elements.nickname.value)>12?'12文字以内で入力してください。':''));
  form.addEventListener('submit',async event=>{event.preventDefault();if(!form.reportValidity())return;const button=form.querySelector('[type=submit]');button.disabled=true;status.textContent='応援を送っています。';const body=form.elements.body.value;try{const d=await call('messages/'+messageClub,{nickname:form.elements.nickname.value,body,website:form.elements.website.value,agree:form.elements.agree.checked});own.unshift({id:d.id,delete_token:d.delete_token,body});own=own.slice(0,20);status.textContent=d.message;storeOwn();paintOwn();form.elements.body.value='';form.elements.body.dispatchEvent(new Event('input'));}catch(e){status.textContent=e.message||'通信できませんでした。';}finally{button.disabled=false;}});
  section.querySelector('[data-cheer-refresh]').addEventListener('click',refresh);paintOwn();
  if('IntersectionObserver'in window){const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){refresh();observer.disconnect();}},{rootMargin:'200px'});observer.observe(section);}else refresh();
})();
