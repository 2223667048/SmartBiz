/* Node-only simulated DOM regression for cross-account DOM state and delayed requests.
   This is not a full browser E2E substitute (manual Playwright/DevTools checks remain). */
const fs=require('fs'),vm=require('vm'),assert=require('assert');
class Element {
  constructor(id=''){this.id=id;this.hidden=false;this.value='';this.textContent='';this.children=[];this.files=[];this.listeners={};this.style={};this.disabled=false;}
  addEventListener(event,fn){this.listeners[event]=fn;}
  replaceChildren(){this.children=[];}
  appendChild(node){this.children.push(node);}
  reset(){if(this.id==='ask-form')elements.question.value='';if(this.id==='upload-form')elements.file.files=[];if(this.id==='gateway-form')elements['gateway-question'].value='';}
  click() {if(this.listeners.click)return this.listeners.click();if(this.onclick)return this.onclick();}
  async submit() {return this.listeners.submit({preventDefault(){}});}
}
const ids=['documents','jobs','users','sources','answer','upload-status','ask-status','users-status','identity',
 'job-metrics','audit-summary','audit-card','audit-refresh','upload-form','ask-form','user-form','login-form','login-status','login-section','workspace','admin-panel','users-card',
 'password','email','question','file','visibility','gateway-report','gateway-status','gateway-answer','gateway-usage','gateway-form','gateway-question','gateway-report-card','gateway-report-refresh','gateway-card','logout','refresh','user-email','user-password','user-role'];
const elements=Object.fromEntries(ids.map(id=>[id,new Element(id)]));
let pendingAsk;
const calls=[];
let activeUser='';
function response(body,status=200){return {ok:status>=200&&status<300,status,async json(){return body}};}
async function fetchFake(path,config={}){
  calls.push({path,config});
  if(path==='/api/v2/auth/login'){
    activeUser=JSON.parse(config.body).email.startsWith('a@')?'A':'B';
    return response({access_token:activeUser});
  }
  if(path==='/api/v2/auth/me')return response({id:activeUser,role:'admin',email:activeUser+'@example.com'});
  if(path==='/api/v2/auth/logout')return response({logged_out:true});
  if(path==='/api/v2/documents')return response([{id:activeUser,filename:activeUser+'-DOC',visibility:'organization',status:'ready',chunk_count:1}]);
  if(path==='/api/v2/jobs/metrics')return response({counts:{succeeded:1},oldest_queued_seconds:0,stale_leases:0});
  if(path==='/api/v2/jobs')return response([{id:activeUser+'-JOB',state:'succeeded',attempts:1,max_attempts:3}]);
  if(path==='/api/v2/users')return response([{email:activeUser+'@example.com',role:'admin',active:true}]);
  if(path==='/api/v2/ask'){
    if(activeUser==='A')return new Promise(resolve=>{pendingAsk=()=>resolve(response({answer:'A-PRIVATE-ANSWER',sources:[{filename:'A-SECRET.md',excerpt:'A-SECRET'}],evidence_status:'ok'}));});
    return response({answer:'B-ANSWER',sources:[],evidence_status:'ok'});
  }
  throw Error('unexpected path: '+path);
}
const window={addEventListener(){}};
const document={getElementById:id=>elements[id],createElement:()=>new Element('created')};
const ctx=vm.createContext({document,window,fetch:fetchFake,AbortController,DOMException,FormData,console,alert:()=>{},confirm:()=>true});
vm.runInContext(fs.readFileSync('web/workspace.js','utf8'),ctx);
(async()=>{
  elements.email.value='a@example.com';elements.password.value='secret-A';
  await elements['login-form'].submit();
  assert.equal(elements.documents.children[0].children[0].textContent,'A-DOC');
  elements.question.value='A secret question';
  const slow=elements['ask-form'].submit();
  assert.equal(typeof pendingAsk,'function');
  // Logout while A's response is still in-flight.
  elements.logout.click();
  assert.equal(elements.documents.children.length,0);
  assert.equal(elements.jobs.children.length,0);
  assert.equal(elements.users.children.length,0);
  assert.equal(elements.sources.children.length,0);
  assert.equal(elements.question.value,'');
  assert.equal(elements.answer.textContent,'');
  assert.equal(elements['gateway-answer'].textContent,'');
  assert.equal(elements['gateway-report'].children.length,0);
  elements.email.value='b@example.com';elements.password.value='secret-B';
  await elements['login-form'].submit();
  pendingAsk();await slow;
  assert.equal(elements.documents.children[0].children[0].textContent,'B-DOC');
  assert.equal(elements.answer.textContent,'');
  assert.equal(elements.sources.children.length,0);
  assert.equal(elements.identity.textContent,'B@example.com / admin');
  assert(calls.some(c=>c.path==='/api/v2/auth/login' && JSON.parse(c.config.body).password==='secret-B'));
  assert(calls.some(c=>c.path==='/api/v2/auth/logout' && c.config.headers.Authorization==='Bearer A'));
  console.log('PASS: A/B logout, clear, stale async answer guard and B credentials');
})().catch(e=>{console.error(e);process.exit(1)});
