"""Build a local browser harness with real assets and isolated fixture APIs."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def build():
    html = (ROOT/'app/templates/index.html').read_text()
    html = re.sub(r'<link\b[^>]*href="/static/([^\"]+\.css)"[^>]*>',
                  lambda match:'<style>'+(ROOT/'app/static'/match[1]).read_text()+'</style>', html)
    scripts = re.findall(r'<script src="/static/([^\"]+)" defer></script>', html)
    html = re.sub(r'<script src="/static/[^\"]+" defer></script>', '', html)
    base = (ROOT/'app/static').as_uri()+'/'
    fixture = (ROOT/'scripts/frontend_stress_browser.js').read_text()
    before, runner = fixture.split('window.runFrontendStress=async()=>', 1)
    source = '\n'.join((ROOT/'app/static'/name).read_text() for name in scripts)
    source = source.replace('/static/', base)
    source = source.replace('</script', '<\\/script')
    html = html.replace('/static/', base)
    control = '''<section style="position:relative;z-index:200;background:#fff;padding:20px;border-bottom:2px solid #7961d6">
<h1>黑马商城前端压力测试</h1><p>真实前端脚本与浏览器 DOM，接口使用独立测试数据。</p>
<button id="stress-run" class="button primary">运行压力测试</button><strong id="stress-status">准备就绪</strong>
<pre id="stress-output" style="white-space:pre-wrap;max-height:65vh;overflow:auto"></pre></section>'''
    html = html.replace('<body>', '<body>'+control)
    html = html.replace('</body>', '<script>'+before+'</script><script>'+source+'</script><script>'
        +'window.runFrontendStress=async()=>'+runner
        +"document.getElementById('stress-run').addEventListener('click',async event=>{event.target.disabled=true;await runFrontendStress();event.target.disabled=false;});"
        +'</script></body>')
    path=ROOT/'reports/frontend-stress.html'
    path.parent.mkdir(exist_ok=True)
    path.write_text(html)
    print(path)


if __name__=='__main__':
    build()
