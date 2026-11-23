"use strict";
(() => {
  const dialog = $('#photo-search-dialog'), status = $('#photo-search-status');
  const submit = $('#photo-search-submit'), preview = $('#photo-search-preview');
  const upload = $('#photo-search-upload'), uploadFile = $('#photo-search-upload-file');
  let generation = 0, available = false, selected = null, previewURL = null, controller = null;

  function clearPhoto() {
    ++generation;
    controller?.abort(); controller = null;
    selected = null; submit.disabled = true; submit.textContent = '开始识别';
    if (previewURL) URL.revokeObjectURL(previewURL);
    previewURL = null; preview.removeAttribute('src');
    $('#photo-search-preview-wrap').hidden = true;
    $('#photo-search-results').hidden = true;
    $('#photo-search-products').innerHTML = '';
    $('#photo-search-keywords').innerHTML = '';
    uploadFile.value = ''; status.textContent = '';
  }

  async function open() {
    clearPhoto(); available = false; upload.disabled = true;
    window.mallSearch?.hide(); showDialog('photo-search-dialog');
    const current = generation;
    status.textContent = '正在检查识别服务…';
    try {
      const result = await api('/search/photo/status');
      if (current !== generation || !dialog.open) return;
      available = result.enabled === true;
      upload.disabled = !available;
      status.textContent = available ? '选择一张商品照片，预览后开始识别。' : '照片识别尚未开通，你仍可使用搜索框输入商品名称。';
    } catch {
      if (current === generation && dialog.open) status.textContent = '暂时无法连接识别服务，请稍后重新打开。';
    }
  }

  function readData(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = () => reject(new Error('图片读取失败，请重新选择。'));
      reader.readAsDataURL(blob);
    });
  }

  async function compress(file) {
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) throw new Error('请选择 JPG、PNG 或 WebP 图片。');
    if (file.size > 15 * 1024 * 1024 || !file.size) throw new Error('原图最大支持 15 MB，请选择较小的图片。');
    const source = URL.createObjectURL(file), image = new Image();
    try {
      await new Promise((resolve, reject) => {
        image.onload = resolve; image.onerror = () => reject(new Error('图片无法读取，请换一张清晰照片。')); image.src = source;
      });
      if (!image.naturalWidth || image.naturalWidth * image.naturalHeight > 32000000) throw new Error('图片尺寸过大，请裁剪后重试。');
      const scale = Math.min(1, 1280 / Math.max(image.naturalWidth, image.naturalHeight));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      const context = canvas.getContext('2d');
      if (!context) throw new Error('浏览器暂不支持图片处理，请使用文字搜索。');
      context.fillStyle = '#fff'; context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.85));
      if (!blob || blob.size > 2 * 1024 * 1024) throw new Error('图片处理失败，请选择较小的图片。');
      return {blob, data: await readData(blob)};
    } finally { URL.revokeObjectURL(source); }
  }

  async function select(event) {
    const file = event.target.files?.[0];
    if (!file || !available) return;
    clearPhoto(); const current = generation;
    status.textContent = '正在准备照片…';
    try {
      const result = await compress(file);
      if (current !== generation || !dialog.open) return;
      selected = result.data; previewURL = URL.createObjectURL(result.blob); preview.src = previewURL;
      $('#photo-search-preview-wrap').hidden = false; submit.disabled = false;
      status.textContent = '照片已准备好，点击“开始识别”。';
    } catch (error) {
      if (current === generation && dialog.open) status.textContent = error.message;
    }
  }

  async function recognize() {
    if (!selected || submit.disabled || !available) return;
    const current = generation;
    controller = new AbortController(); submit.disabled = true; submit.textContent = '正在识别…';
    $('#photo-search-results').hidden = true;
    status.textContent = '正在识别商品并查找商城好物，请稍候…';
    try {
      const result = await api('/search/photo', {method: 'POST', signal: controller.signal,
        body: JSON.stringify({filename: 'photo.jpg', data_base64: selected})});
      if (current !== generation || !dialog.open) return;
      const keywords = result.keywords || [], products = result.items || [];
      $('#photo-search-results').hidden = false;
      $('#photo-search-keywords').innerHTML = keywords.map(word => '<button type="button" data-photo-keyword="' + escapeHTML(word) + '">' + escapeHTML(word) + ' ↗</button>').join('');
      $('#photo-search-products').innerHTML = products.map(product => '<button type="button" class="photo-search-product" data-photo-product="' + product.id + '"><img src="' + escapeHTML(image(product)) + '" alt=""><span><strong>' + escapeHTML(product.name) + '</strong><small>' + escapeHTML(product.store_name) + '</small><b>¥' + money(product.price) + '</b></span></button>').join('');
      $('#photo-search-result-hint').textContent = products.length ? (result.has_more ? '相关商品较多，当前展示前 12 件；点击查看详情，也可点击关键词继续搜索。' : '找到 ' + products.length + ' 件相关商品，点击查看详情，也可点击关键词继续搜索。') : keywords.length ? '已识别出商品类型，商城暂未找到相关在售商品。可以点击关键词继续搜索。' : '没有识别到清晰的商品，请靠近主体重拍。';
      status.textContent = '识别完成。结果仅供选购参考，请核对商品详情。';
    } catch (error) {
      if (current === generation && dialog.open && error.name !== 'AbortError') status.textContent = friendlyError(error);
    } finally {
      if (current === generation && dialog.open) { controller = null; submit.disabled = !selected; submit.textContent = '重新识别'; }
    }
  }

  $('#photo-search-open').addEventListener('click', open);
  upload.addEventListener('click', () => uploadFile.click());
  uploadFile.addEventListener('change', select);
  $('#photo-search-remove').addEventListener('click', clearPhoto);
  submit.addEventListener('click', recognize);
  dialog.addEventListener('close', clearPhoto);
  $('#photo-search-results').addEventListener('click', async event => {
    const button = event.target.closest('[data-photo-keyword],[data-photo-product]');
    if (!button) return;
    try {
      closeDialog('photo-search-dialog');
      if (button.dataset.photoProduct) await showProduct(Number(button.dataset.photoProduct));
      else {
        window.mallPages?.catalog({home:true});
        state.q = button.dataset.photoKeyword; state.category = ''; state.sort = 'recommended'; state.page = 1; state.onSale = false; state.storeId = null;
        $('#search-input').value = state.q; $('#store-filter').value = ''; $('#sort-select').value = state.sort;
        document.querySelectorAll('[data-category]').forEach(tab => tab.classList.toggle('active', tab.dataset.category === ''));
        $('#search-form').requestSubmit();
      }
    } catch (error) { toast(friendlyError(error)); }
  });
})();
