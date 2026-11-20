'use strict';

const $ = (id) => document.getElementById(id);

const state = {
  tab: 'lyrics',
  config: null,
  defaults: null,
  balance: null,
  estimate: null,
  job: null,
  polling: false,
  pollErrors: 0,
  titleTouched: false,
  styleTouched: false,
  pending: null
};

const el = {
  apiPill: $('apiPill'),
  apiDot: $('apiDot'),
  apiText: $('apiText'),
  balanceText: $('balanceText'),
  apiBtn: $('apiBtn'),
  apiDialog: $('apiDialog'),
  apiForm: $('apiForm'),
  apiKey: $('apiKey'),
  showKey: $('showKey'),
  baseUrl: $('baseUrl'),
  currentKey: $('currentKey'),
  apiError: $('apiError'),
  saveApiBtn: $('saveApiBtn'),
  closeApi: $('closeApi'),
  cancelApi: $('cancelApi'),
  tabLyrics: $('tabLyrics'),
  tabIdea: $('tabIdea'),
  panelLyrics: $('panelLyrics'),
  panelIdea: $('panelIdea'),
  lyrics: $('lyrics'),
  lyricsFile: $('lyricsFile'),
  fileName: $('fileName'),
  charCount: $('charCount'),
  clearLyrics: $('clearLyrics'),
  refineBtn: $('refineBtn'),
  refineNotes: $('refineNotes'),
  idea: $('idea'),
  draftBtn: $('draftBtn'),
  aiLyricsBtn: $('aiLyricsBtn'),
  advanced: $('advanced'),
  title: $('title'),
  model: $('model'),
  styleTags: $('styleTags'),
  styleCount: $('styleCount'),
  resetStyle: $('resetStyle'),
  maxMode: $('maxMode'),
  estimateText: $('estimateText'),
  balanceHint: $('balanceHint'),
  generateBtn: $('generateBtn'),
  generateLabel: $('generateLabel'),
  jobCard: $('jobCard'),
  jobSpinner: $('jobSpinner'),
  jobTitle: $('jobTitle'),
  jobStatus: $('jobStatus'),
  jobError: $('jobError'),
  progressBar: $('progressBar'),
  resultCard: $('resultCard'),
  resultMeta: $('resultMeta'),
  songList: $('songList'),
  confirmDialog: $('confirmDialog'),
  confirmForm: $('confirmForm'),
  confirmLyrics: $('confirmLyrics'),
  confirmStyle: $('confirmStyle'),
  confirmPrice: $('confirmPrice'),
  confirmError: $('confirmError'),
  confirmBtn: $('confirmBtn'),
  closeConfirm: $('closeConfirm'),
  cancelConfirm: $('cancelConfirm'),
  aiDialog: $('aiDialog'),
  aiForm: $('aiForm'),
  aiConsent: $('aiConsent'),
  aiError: $('aiError'),
  aiConfirmBtn: $('aiConfirmBtn'),
  closeAi: $('closeAi'),
  cancelAi: $('cancelAi'),
  toast: $('toast')
};

const STATUS_TEXT = {
  submitting: '正在提交…',
  queued: '排队中…',
  submitted: '已提交…',
  pending: '等待资源…',
  preparing_source: '准备素材…',
  waiting_account: '等待账户…',
  processing: '合成中…',
  playable: '已可试听',
  streaming: '生成中，可试听…',
  completed: '完成',
  failed: '生成失败',
  canceled: '已取消',
  unknown: '处理中…'
};

const STATUS_PROGRESS = {
  submitting: 12,
  queued: 22,
  submitted: 22,
  pending: 26,
  preparing_source: 32,
  waiting_account: 32,
  processing: 58,
  playable: 82,
  streaming: 82,
  completed: 100,
  failed: 100,
  canceled: 100,
  unknown: 36
};

function asText(value) {
  return value === null || value === undefined ? '' : String(value);
}

function escapeHtml(value) {
  return asText(value).replace(/[&<>"']/g, function (character) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character];
  });
}

function countChars(value) {
  return Array.from(asText(value)).length;
}

function formatMoney(value) {
  const amount = Number(value);
  return Number.isFinite(amount) ? '¥' + amount.toFixed(2) : '¥ --';
}

function formatDuration(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return '时长未知';
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return minutes + ':' + String(rest).padStart(2, '0');
}

let toastTimer = 0;
function toast(message) {
  el.toast.textContent = asText(message);
  el.toast.classList.add('show');
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(function () {
    el.toast.classList.remove('show');
  }, 3400);
}

function setApiStatus(kind, label) {
  el.apiDot.className = 'dot' + (kind ? ' ' + kind : '');
  el.apiText.textContent = label;
}

function isRateLimited(error) {
  const message = String((error && error.message) || '');
  return !!error && (error.status === 429 || error.code === 'SUNO_RATE_LIMITED' || /429|频繁/.test(message));
}

function showDialog(dialog) {
  if (typeof dialog.showModal === 'function') {
    if (!dialog.open) dialog.showModal();
  } else {
    dialog.setAttribute('open', '');
  }
}

function closeDialog(dialog) {
  if (typeof dialog.close === 'function') {
    if (dialog.open) dialog.close();
  } else {
    dialog.removeAttribute('open');
  }
}

async function request(path, options) {
  const opts = options || {};
  const headers = Object.assign({}, opts.headers || {});
  if (opts.body !== undefined && opts.body !== null) headers['Content-Type'] = 'application/json';
  if ((opts.method || 'GET').toUpperCase() !== 'GET') headers['X-Sifeng-Client'] = '1';
  let response;
  try {
    response = await fetch(path, Object.assign({}, opts, { headers: headers }));
  } catch (error) {
    throw new Error('无法连接本地服务，请确认程序仍在运行。');
  }
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (!response.ok || (data && data.ok === false)) {
    const message = (data && (data.error || data.message)) || ('请求失败（' + response.status + '）');
    const failure = new Error(message);
    failure.code = data && data.code;
    failure.details = data && data.details;
    failure.status = response.status;
    throw failure;
  }
  return data || {};
}

function post(path, body) {
  return request(path, { method: 'POST', body: JSON.stringify(body || {}) });
}

function updateCounts() {
  el.charCount.textContent = countChars(el.lyrics.value) + ' 字';
  const styleLength = countChars(el.styleTags.value);
  el.styleCount.textContent = styleLength + ' / 1000';
  el.styleCount.classList.toggle('style-count-over', styleLength > 1000);
}

function renderNotes(notes) {
  el.refineNotes.innerHTML = '';
  (notes || []).forEach(function (note) {
    const chip = document.createElement('span');
    chip.textContent = asText(note);
    el.refineNotes.appendChild(chip);
  });
}

function syncConfig() {
  const config = state.config || {};
  if (config.configured) {
    setApiStatus('ok', 'API 已配置');
    el.currentKey.textContent = '当前 Key：' + (config.key_masked || '已保存');
  } else {
    setApiStatus('', '未配置 API');
    el.currentKey.textContent = '尚未配置 Key';
  }
  el.baseUrl.value = config.base_url || 'https://www.suno-api.io';
  el.balanceText.textContent = state.balance ? '余额 ' + formatMoney(state.balance.amount) : '余额 --';
}

function applyEstimateFromPrices(prices) {
  if (!prices) return false;
  const base = Number(prices['generation.per_request']);
  if (!Number.isFinite(base)) return false;
  const factor = el.maxMode.checked ? Number(prices['generation.max_mode_factor'] || 2) : 1;
  const amount = Math.round(base * factor * 100) / 100;
  state.estimate = {
    amount: amount,
    currency: 'CNY',
    price_key: 'generation.per_request',
    max_mode: el.maxMode.checked
  };
  el.estimateText.textContent = formatMoney(amount);
  el.balanceHint.textContent = '生成 2 个版本 · ' + (state.balance ? '余额 ' + formatMoney(state.balance.amount) : '余额 --');
  return true;
}

function setTab(tab) {
  state.tab = tab === 'idea' ? 'idea' : 'lyrics';
  const lyricsActive = state.tab === 'lyrics';
  el.tabLyrics.classList.toggle('active', lyricsActive);
  el.tabIdea.classList.toggle('active', !lyricsActive);
  el.tabLyrics.setAttribute('aria-selected', String(lyricsActive));
  el.tabIdea.setAttribute('aria-selected', String(!lyricsActive));
  el.panelLyrics.classList.toggle('hidden', !lyricsActive);
  el.panelIdea.classList.toggle('hidden', lyricsActive);
}

function openApiDialog() {
  el.apiError.classList.add('hidden');
  el.apiError.textContent = '';
  el.apiKey.value = '';
  el.apiKey.type = 'password';
  el.showKey.checked = false;
  const config = state.config || {};
  el.baseUrl.value = config.base_url || 'https://www.suno-api.io';
  el.currentKey.textContent = config.configured
    ? '当前 Key：' + (config.key_masked || '已保存')
    : '尚未配置 Key';
  showDialog(el.apiDialog);
}

function showApiError(message) {
  el.apiError.textContent = message;
  el.apiError.classList.remove('hidden');
}

async function loadConfig() {
  const data = await request('/api/config');
  state.config = data.config || {};
  state.defaults = data.defaults || {};
  if (!state.styleTouched) {
    el.styleTags.value = state.defaults.style || '';
  }
  if (!state.titleTouched) {
    el.title.value = state.defaults.title || '司凤新作';
  }
  if (state.defaults.model) {
    el.model.value = state.defaults.model;
  }
  updateCounts();
  syncConfig();
  if (state.config.configured) {
    await testApi();
  } else {
    el.estimateText.textContent = '先配置 API';
    el.balanceHint.textContent = '点击右上角 API 设置开始';
  }
}

async function testApi() {
  setApiStatus('loading', '检查连接…');
  try {
    const data = await post('/api/config/test', {});
    state.config = data.config || state.config || {};
    state.balance = data.balance || null;
    syncConfig();
    setApiStatus('ok', 'API 已连接');
    el.balanceText.textContent = state.balance ? '余额 ' + formatMoney(state.balance.amount) : '余额 --';
    if (!applyEstimateFromPrices(data.prices)) await refreshEstimate();
    return true;
  } catch (error) {
    const limited = isRateLimited(error);
    setApiStatus(limited ? 'warn' : 'err', limited ? '请求太频繁' : '连接失败');
    el.balanceText.textContent = '余额 --';
    el.estimateText.textContent = '暂不可用';
    el.balanceHint.textContent = error.message;
    return false;
  }
}

async function refreshEstimate() {
  if (!state.config || !state.config.configured) {
    el.estimateText.textContent = '先配置 API';
    el.balanceHint.textContent = '点击右上角 API 设置开始';
    return null;
  }
  try {
    const data = await post('/api/estimate', {
      mode: 'fresh',
      max_mode: el.maxMode.checked
    });
    state.estimate = data.estimate || null;
    el.estimateText.textContent = state.estimate ? formatMoney(state.estimate.amount) : '¥ --';
    el.balanceHint.textContent = '生成 2 个版本 · ' + (state.balance ? '余额 ' + formatMoney(state.balance.amount) : '余额 --');
    return state.estimate;
  } catch (error) {
    state.estimate = null;
    el.estimateText.textContent = '¥ --';
    el.balanceHint.textContent = error.message;
    return null;
  }
}

async function refineLyrics() {
  const source = el.lyrics.value.trim();
  if (!source) {
    setTab('lyrics');
    toast('先贴一段歌词');
    el.lyrics.focus();
    return;
  }
  const original = el.refineBtn.textContent;
  el.refineBtn.disabled = true;
  el.refineBtn.textContent = '司凤化中…';
  try {
    const data = await post('/api/lyrics/refine', {
      lyrics: source,
      title: el.title.value.trim()
    });
    el.lyrics.value = data.lyrics || source;
    if (data.title && !state.titleTouched) el.title.value = data.title;
    renderNotes(data.notes || []);
    updateCounts();
    toast('已经司凤化，可以继续改');
  } catch (error) {
    toast(error.message);
  } finally {
    el.refineBtn.disabled = false;
    el.refineBtn.textContent = original;
  }
}

async function draftLyrics(silent) {
  const idea = el.idea.value.trim();
  if (!idea) {
    if (!silent) {
      setTab('idea');
      toast('先写一句想法');
      el.idea.focus();
    }
    return '';
  }
  const original = el.draftBtn.textContent;
  el.draftBtn.disabled = true;
  el.draftBtn.textContent = '起草中…';
  try {
    const data = await post('/api/lyrics/draft', { idea: idea });
    el.lyrics.value = data.lyrics || '';
    if (data.title && !state.titleTouched) el.title.value = data.title;
    renderNotes(['已在本机起草，可继续修改或直接生成']);
    updateCounts();
    setTab('lyrics');
    if (!silent) toast('草稿已放进歌词框');
    return el.lyrics.value;
  } catch (error) {
    if (!silent) toast(error.message);
    throw error;
  } finally {
    el.draftBtn.disabled = false;
    el.draftBtn.textContent = original;
  }
}

async function ensureLyricsForGenerate() {
  const existing = el.lyrics.value.trim();
  if (existing) return existing;
  if (el.idea.value.trim()) {
    const drafted = await draftLyrics(true);
    if (drafted && drafted.trim()) {
      toast('已用想法自动起草');
      return drafted.trim();
    }
  }
  throw new Error('先贴歌词，或者写一句想法');
}

async function prepareGenerate() {
  if (state.polling) {
    toast('当前任务还在生成，请等它完成');
    return;
  }
  if (!state.config || !state.config.configured) {
    openApiDialog();
    toast('先配置 API');
    return;
  }
  const style = el.styleTags.value.trim();
  if (!style) {
    el.advanced.open = true;
    toast('风格提示词不能为空');
    return;
  }
  el.generateBtn.disabled = true;
  el.generateLabel.textContent = '准备中…';
  try {
    const lyrics = await ensureLyricsForGenerate();
    await refreshEstimate();
    const amount = state.estimate ? Number(state.estimate.amount) : NaN;
    if (!Number.isFinite(amount)) throw new Error('无法获取实时价格，请检查 API 连接');
    state.pending = {
      lyrics: lyrics,
      style: style,
      title: el.title.value.trim() || '司凤新作',
      model: el.model.value,
      max_mode: el.maxMode.checked,
      amount: amount
    };
    el.confirmLyrics.textContent = countChars(lyrics) + ' 字';
    el.confirmStyle.textContent = countChars(style) + ' 字';
    el.confirmPrice.textContent = formatMoney(amount);
    el.confirmBtn.textContent = '确认并支付 ' + formatMoney(amount);
    el.confirmError.classList.add('hidden');
    el.confirmError.textContent = '';
    showDialog(el.confirmDialog);
  } catch (error) {
    toast(error.message);
  } finally {
    el.generateBtn.disabled = false;
    el.generateLabel.textContent = '生成司凤音乐';
  }
}

function startPolling(job) {
  state.job = job || {};
  state.polling = true;
  state.pollErrors = 0;
  el.generateBtn.disabled = true;
  el.generateLabel.textContent = '生成中…';
  el.jobCard.classList.remove('hidden');
  el.resultCard.classList.add('hidden');
  el.jobSpinner.className = 'spinner';
  el.jobTitle.textContent = '正在生成';
  el.jobStatus.textContent = '已提交，等待云端返回…';
  el.jobError.classList.add('hidden');
  el.jobError.textContent = '';
  el.progressBar.style.width = '12%';
  pollJob();
}

async function pollJob() {
  if (!state.polling) return;
  const job = state.job || {};
  const params = new URLSearchParams();
  if (job.id) params.set('job_id', job.id);
  if (job.request_id) params.set('request_id', job.request_id);
  if (!params.toString()) {
    state.polling = false;
    failJob({ status: 'failed', error: '任务信息不完整，请重新提交。' });
    return;
  }
  try {
    const data = await request('/api/job?' + params.toString());
    const latest = data.job || {};
    state.job = latest;
    state.pollErrors = 0;
    const status = latest.status || 'unknown';
    el.jobStatus.textContent = (STATUS_TEXT[status] || '处理中…') + (latest.request_id ? ' · ' + latest.request_id : '');
    el.progressBar.style.width = (STATUS_PROGRESS[status] || 36) + '%';
    if (status === 'completed') {
      state.polling = false;
      finishJob(latest);
      return;
    }
    if (status === 'failed' || status === 'canceled') {
      state.polling = false;
      failJob(latest);
      return;
    }
    window.setTimeout(pollJob, 5000);
  } catch (error) {
    state.pollErrors += 1;
    if (state.pollErrors >= 8) {
      state.polling = false;
      failJob(Object.assign({}, job, { status: 'failed', error: error.message }));
      return;
    }
    el.jobStatus.textContent = '网络波动，正在重试…';
    window.setTimeout(pollJob, 6000);
  }
}

function finishJob(job) {
  const songs = (job.songs || []).filter(function (song) {
    return song && song.song_id;
  });
  el.progressBar.style.width = '100%';
  if (!songs.length) {
    failJob(Object.assign({}, job, { error: job.error || '任务完成但没有返回可播放的歌曲。' }));
    return;
  }
  el.jobCard.classList.add('hidden');
  renderSongs(job, songs);
  el.resultCard.classList.remove('hidden');
  el.generateBtn.disabled = false;
  el.generateLabel.textContent = '生成司凤音乐';
  el.resultCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
  toast('生成完成，' + songs.length + ' 个版本已就绪');
}

function failJob(job) {
  el.generateBtn.disabled = false;
  el.generateLabel.textContent = '生成司凤音乐';
  el.jobSpinner.className = 'spinner fail';
  el.jobTitle.textContent = '生成失败';
  el.jobStatus.textContent = '任务未完成，请检查错误信息后重试。';
  el.jobError.textContent = job.error || '未知错误。';
  el.jobError.classList.remove('hidden');
  el.progressBar.style.width = '100%';
}

function renderSongs(job, songs) {
  el.resultMeta.textContent = songs.length + ' 个版本 · ' + (job.title || '司凤作品');
  el.songList.innerHTML = '';
  songs.forEach(function (song, index) {
    const title = song.title || job.title || '司凤作品';
    const article = document.createElement('article');
    article.className = 'song';
    const player = song.audio_url
      ? '<audio controls preload="metadata" src="' + escapeHtml(song.audio_url) + '"></audio>'
      : '<p class="hint">音频地址暂不可用，请稍后刷新。</p>';
    article.innerHTML =
      '<div class="song-top">' +
        '<span class="song-index">' + String(index + 1).padStart(2, '0') + '</span>' +
        '<div><h3>' + escapeHtml(title) + '</h3><p>' + escapeHtml(formatDuration(song.duration)) + '</p></div>' +
      '</div>' +
      player +
      '<div class="song-actions">' +
        '<button class="ghost btn-sm" type="button" data-download="mp3" data-song-id="' + escapeHtml(song.song_id) + '" data-title="' + escapeHtml(title) + '">下载 MP3</button>' +
        '<button class="ghost btn-sm" type="button" data-download="wav" data-song-id="' + escapeHtml(song.song_id) + '" data-title="' + escapeHtml(title) + '">下载 WAV</button>' +
      '</div>';
    el.songList.appendChild(article);
  });
}

async function downloadSong(button) {
  const format = button.getAttribute('data-download');
  const songId = button.getAttribute('data-song-id');
  const title = button.getAttribute('data-title') || '司凤作品';
  const original = button.textContent;
  button.disabled = true;
  button.textContent = format === 'wav' ? '准备 WAV…' : '准备 MP3…';
  try {
    const data = await post('/api/download', {
      song_id: songId,
      title: title,
      format: format
    });
    const file = data.file || {};
    if (!file.url) throw new Error('下载地址缺失');
    const link = document.createElement('a');
    link.href = file.url;
    link.download = file.filename || ('sifeng.' + format);
    document.body.appendChild(link);
    link.click();
    link.remove();
    toast('已保存到本机 outputs/');
  } catch (error) {
    toast('下载失败：' + error.message);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function bindEvents() {
  el.apiPill.addEventListener('click', openApiDialog);
  el.apiBtn.addEventListener('click', openApiDialog);
  el.closeApi.addEventListener('click', function () { closeDialog(el.apiDialog); });
  el.cancelApi.addEventListener('click', function () { closeDialog(el.apiDialog); });
  el.closeConfirm.addEventListener('click', function () { closeDialog(el.confirmDialog); });
  el.cancelConfirm.addEventListener('click', function () { closeDialog(el.confirmDialog); });
  el.closeAi.addEventListener('click', function () { closeDialog(el.aiDialog); });
  el.cancelAi.addEventListener('click', function () { closeDialog(el.aiDialog); });

  [el.apiDialog, el.confirmDialog, el.aiDialog].forEach(function (dialog) {
    dialog.addEventListener('click', function (event) {
      if (event.target === dialog) closeDialog(dialog);
    });
  });

  el.tabLyrics.addEventListener('click', function () { setTab('lyrics'); });
  el.tabIdea.addEventListener('click', function () { setTab('idea'); });
  el.lyrics.addEventListener('input', updateCounts);
  el.styleTags.addEventListener('input', function () {
    state.styleTouched = true;
    updateCounts();
  });
  el.title.addEventListener('input', function () {
    state.titleTouched = true;
  });
  el.maxMode.addEventListener('change', refreshEstimate);
  el.resetStyle.addEventListener('click', function () {
    el.styleTags.value = (state.defaults && state.defaults.style) || '';
    state.styleTouched = false;
    updateCounts();
    toast('已恢复司凤默认提示词');
  });
  el.clearLyrics.addEventListener('click', function () {
    el.lyrics.value = '';
    el.fileName.textContent = '只在本机读取，不上传';
    renderNotes([]);
    updateCounts();
  });
  el.refineBtn.addEventListener('click', refineLyrics);
  el.draftBtn.addEventListener('click', function () { draftLyrics(false).catch(function () {}); });
  el.generateBtn.addEventListener('click', prepareGenerate);

  el.lyricsFile.addEventListener('change', async function () {
    const file = el.lyricsFile.files && el.lyricsFile.files[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
      toast('文件太大，请选择 2MB 以内的文本');
      el.lyricsFile.value = '';
      return;
    }
    try {
      const buffer = await file.arrayBuffer();
      let content = '';
      try {
        content = new TextDecoder('utf-8', { fatal: true }).decode(buffer);
      } catch (error) {
        try {
          content = new TextDecoder('gbk').decode(buffer);
        } catch (fallbackError) {
          content = new TextDecoder('utf-8').decode(buffer);
        }
      }
      el.lyrics.value = content.replace(/^\uFEFF/, '').trim();
      el.fileName.textContent = file.name;
      updateCounts();
      setTab('lyrics');
      toast('已读取本地歌词');
    } catch (error) {
      toast('读取失败：' + error.message);
    } finally {
      el.lyricsFile.value = '';
    }
  });

  el.aiLyricsBtn.addEventListener('click', function () {
    if (!el.idea.value.trim()) {
      setTab('idea');
      toast('先写一句想法');
      el.idea.focus();
      return;
    }
    el.aiConsent.checked = false;
    el.aiConfirmBtn.disabled = true;
    el.aiError.classList.add('hidden');
    el.aiError.textContent = '';
    showDialog(el.aiDialog);
  });

  el.aiConsent.addEventListener('change', function () {
    el.aiConfirmBtn.disabled = !el.aiConsent.checked;
  });

  el.aiForm.addEventListener('submit', async function (event) {
    event.preventDefault();
    if (!el.aiConsent.checked) return;
    const original = el.aiConfirmBtn.textContent;
    el.aiConfirmBtn.disabled = true;
    el.aiConfirmBtn.textContent = '写词中…';
    el.aiError.classList.add('hidden');
    try {
      const data = await post('/api/lyrics/generate', {
        instruction: el.idea.value.trim(),
        style: el.styleTags.value.trim(),
        title: el.title.value.trim(),
        model: el.model.value,
        refine: true,
        confirm_external: true,
        confirm_cost: true
      });
      el.lyrics.value = data.lyrics || '';
      if (data.title && !state.titleTouched) el.title.value = data.title;
      renderNotes(data.notes || []);
      updateCounts();
      closeDialog(el.aiDialog);
      setTab('lyrics');
      toast('AI 写词完成，可继续修改');
    } catch (error) {
      el.aiError.textContent = error.message;
      el.aiError.classList.remove('hidden');
    } finally {
      el.aiConfirmBtn.textContent = original;
      el.aiConfirmBtn.disabled = !el.aiConsent.checked;
    }
  });

  el.apiForm.addEventListener('submit', async function (event) {
    event.preventDefault();
    const keyValue = el.apiKey.value.trim();
    const urlValue = el.baseUrl.value.trim();
    if (keyValue && !/^[A-Za-z0-9._~+/=-]+$/.test(keyValue)) {
      showApiError('API Key 只能包含英文字母、数字和 - _ . ~ + / =；请不要粘贴带省略号的脱敏 Key。');
      el.apiKey.focus();
      return;
    }
    if (urlValue && /[^\x00-\x7F]/.test(urlValue)) {
      showApiError('Base URL 只能包含 ASCII 字符，请填写 https://www.suno-api.io。');
      el.baseUrl.focus();
      return;
    }
    const original = el.saveApiBtn.textContent;
    el.saveApiBtn.disabled = true;
    el.saveApiBtn.textContent = '测试中…';
    el.apiError.classList.add('hidden');
    try {
      const data = await post('/api/config', {
        api_key: keyValue,
        base_url: urlValue
      });
      state.config = data.config || {};
      state.balance = data.balance || null;
      el.apiKey.value = '';
      syncConfig();
      setApiStatus('ok', 'API 已连接');
      closeDialog(el.apiDialog);
      toast('API 已连接');
      if (!applyEstimateFromPrices(data.prices)) await refreshEstimate();
    } catch (error) {
      const limited = isRateLimited(error);
      showApiError(error.message);
      setApiStatus(limited ? 'warn' : 'err', limited ? '请求太频繁' : '连接失败');
    } finally {
      el.saveApiBtn.disabled = false;
      el.saveApiBtn.textContent = original;
    }
  });

  el.showKey.addEventListener('change', function () {
    el.apiKey.type = el.showKey.checked ? 'text' : 'password';
  });

  el.confirmForm.addEventListener('submit', async function (event) {
    event.preventDefault();
    const pending = state.pending;
    if (!pending) return;
    const original = el.confirmBtn.textContent;
    el.confirmBtn.disabled = true;
    el.confirmBtn.textContent = '提交中…';
    el.confirmError.classList.add('hidden');
    try {
      const idempotencyKey = window.crypto && typeof window.crypto.randomUUID === 'function'
        ? window.crypto.randomUUID()
        : 'sifeng-' + Date.now() + '-' + Math.random().toString(16).slice(2);
      const data = await post('/api/generate', {
        mode: 'fresh',
        title: pending.title,
        style_tags: pending.style,
        lyrics: pending.lyrics,
        model: pending.model,
        max_mode: pending.max_mode,
        expected_amount: pending.amount,
        confirm_cost: true,
        idempotency_key: idempotencyKey
      });
      closeDialog(el.confirmDialog);
      toast('已提交，正在生成 2 个版本');
      startPolling(data.job || {});
    } catch (error) {
      el.confirmError.textContent = error.message;
      el.confirmError.classList.remove('hidden');
      if (error.code === 'PRICE_CHANGED') await refreshEstimate();
    } finally {
      el.confirmBtn.disabled = false;
      el.confirmBtn.textContent = original;
    }
  });

  el.songList.addEventListener('click', function (event) {
    const button = event.target.closest('button[data-download]');
    if (!button) return;
    downloadSong(button);
  });
}

async function init() {
  bindEvents();
  updateCounts();
  try {
    await loadConfig();
  } catch (error) {
    setApiStatus('err', '本地服务异常');
    toast(error.message);
  }
}

init();

