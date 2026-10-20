"""Browser end-to-end smoke test without an externally reachable server.
Intercepts requests in Chromium and forwards them into FastAPI TestClient.
"""
import os,sys,tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
temp=tempfile.TemporaryDirectory()
os.environ['DATABASE_URL']='sqlite:///'+str(Path(temp.name)/'test.sqlite3')
sys.path.insert(0,str(root))
from app import app
from fastapi.testclient import TestClient
client=TestClient(app)
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--no-proxy-server'])
 page=browser.new_page(viewport={'width':1440,'height':900})
 errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 def handler(route):
  req=route.request
  from urllib.parse import urlsplit
  path=urlsplit(req.url).path
  if urlsplit(req.url).query:path+='?'+urlsplit(req.url).query
  headers={k:v for k,v in req.headers.items() if k.lower() not in {'host','connection','content-length'}}
  r=client.request(req.method,path,headers=headers,content=req.post_data_buffer)
  respheaders={k:v for k,v in r.headers.items() if k.lower() not in {'content-length','transfer-encoding','content-encoding'}}
  route.fulfill(status=r.status_code,headers=respheaders,body=r.content)
 page.route('**/*',handler)
 page.goto('http://myco.test/',wait_until='domcontentloaded')
 print('auth page:',page.locator('h2').first.inner_text())
 page.locator('input[name="name"]').fill('Tester')
 page.locator('input[name="farm_name"]').fill('Demo Mushroom Farm')
 page.locator('input[name="email"]').fill('grower@example.com')
 page.locator('input[name="password"]').fill('CorrectHorse123!')
 page.locator('#authForm button[type=submit]').click()
 page.wait_for_selector('.sidebar',timeout=15000)
 print('shell:',page.locator('h1').first.inner_text())
 page.locator('button[data-action="create"][data-id="kind:batch"]').first.click()
 page.locator('#editorForm input[name="name"]').fill('First Lion’s Mane')
 page.locator('#editorForm input[name="species"]').fill('Lion’s mane')
 page.locator('#editorForm input[name="blocks"]').fill('30')
 page.locator('#editorForm input[name="dry_lb"]').fill('2.4')
 page.locator('button[type="submit"][form="editorForm"]').click()
 page.wait_for_selector('.toast')
 print('save:',page.locator('.toast').last.inner_text())
 page.locator('button[data-action="nav"][data-id="harvests"]').first.click()
 page.locator('button[data-action="new-harvest"]').first.click()
 page.locator('#harvestForm input[name="weight_lb"]').fill('8.5')
 page.locator('button[type="submit"][form="harvestForm"]').click()
 page.wait_for_timeout(250)
 page.locator('button[data-action="nav"][data-id="inventory"]').first.click()
 page.wait_for_timeout(200)
 page.screenshot(path='/mnt/data/MycoOS-Cloud-Preview.png',full_page=True)
 print('inventory:',page.locator('main').inner_text()[-450:])
 print('browser JS errors:',errors)
 assert not errors
 browser.close()
