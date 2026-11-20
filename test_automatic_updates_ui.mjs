import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('webapp/js/settings.js', import.meta.url), 'utf8').split('// webapp/js/settings.js')[0];
const elements = Object.fromEntries(['settingsAutomaticUpdates','settingsUpdatesHelp','settingsUpdateAvailable','settingsStatus'].map(k=>[k,{}]));
const calls=[];
const win={_settingsVersionState:{behind_by:1,remote_version:'5.0.76'},open:(...a)=>calls.push(a)};
const context=vm.createContext({window:win,BOOT:{distribution:'pinokio'},document:{getElementById:k=>elements[k]},URLSearchParams,
  _settingsCache:{settings:{update_automatically:false}},
  T:(k,s,args={})=>Object.entries(args).reduce((v,[key,val])=>v.replace('{'+key+'}',val),s),
  siteLink:path=>'https://phosphene.video'+path,
  fetch:async (url,opts)=>{calls.push({url,...opts});return {ok:true};},versionDoPull:opts=>calls.push(opts)});
vm.runInContext(source,context);
context.renderAutomaticUpdates({update_automatically:false});
const box=elements.settingsAutomaticUpdates;
assert.equal(box.checked,false);
assert.equal(elements.settingsUpdateAvailable.textContent,'Update available (5.0.76)');
box.checked=true;await context.settingsAutomaticUpdatesChange(box);
assert.equal(calls[0].body.get('update_automatically'),'true');assert.equal(box.disabled,false);
context.fetch=async()=>{throw Error('offline')};box.checked=false;
await context.settingsAutomaticUpdatesChange(box);assert.equal(box.checked,true);assert.equal(box.disabled,false);
context.BOOT.distribution='mac-app';context.renderAutomaticUpdates({});assert.equal(box.disabled,true);
win.phosAppUpdate={caps:['updateAll','automaticUpdates'],automatic:false,behind:true,target:'5.0.77'};
win.webkit={messageHandlers:{phosphene:{postMessage:m=>calls.push(m)}}};
context.renderAutomaticUpdates({update_automatically:true});assert.equal(box.checked,false);assert.equal(box.disabled,false);
box.checked=true;await context.settingsAutomaticUpdatesChange(box);
assert.equal(calls.at(-1).type,'setAutomaticUpdates');assert.equal(calls.at(-1).enabled,true);
win.phosAppUpdate.automatic=true;context.renderAutomaticUpdates();assert.equal(box.checked,true);assert.equal(box.disabled,false);
context.settingsUpdateNow();assert.equal(calls.at(-1).type,'updateAll');
win.phosAppUpdate=null;context.settingsUpdateNow();assert.equal(calls.at(-1).skipConfirm,true);
context.settingsUpdatesWhatsNew();assert.equal(calls.at(-1)[0],'https://phosphene.video/changelog/');
for(const lang of ['de','es','fr','it','ja','ko','pt','ru','zh-Hans']) {
 const strings=JSON.parse(fs.readFileSync(new URL(`i18n/studio/${lang}.json`,import.meta.url))).strings;
 for(const key of ['updates','update_automatically','update_automatically_help','update_app_for_control','update_all_help','update_available','update','whats_new','update_choice_not_saved']) assert.ok(strings['settings.'+key],lang+' '+key);
 assert.ok(strings['settings.update_available'].includes('{version}'));
}
console.log('PASS persisted UI state, saved changes, failed-save rollback, old-app guard, native acknowledgement, manual update, release notes, all nine translations');
