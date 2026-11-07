(() => {
  const assetBase = new URL('.', document.currentScript.src);
  const arenaSource = new URL('data/demo-arena.json', assetBase);
  const gallerySource = new URL('data/gallery.json', location.href);
  const root = document.getElementById('arena-v2');
  const main = root.querySelector('[data-main]');
  const nav = root.querySelector('[data-nav]');
  const languagePicker = root.querySelector('[data-language-picker]');
  const languageTrigger = languagePicker.querySelector('summary');
  const languageLabel = languagePicker.querySelector('.desktop-language-label');
  const languageMenu = languagePicker.querySelector('.desktop-language-menu');
  const languageOptions = [...languageMenu.querySelectorAll('[data-language]')];
  const DAY = 86400000;
  const MINUTE = 60000;
  const MINUTES_PER_DAY = 24 * 60;
  const ARENA_REFRESH_INTERVAL = 5 * MINUTE;
  const snapshot = Date.now();
  const localZone = new Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const firstDate = Date.parse('2026-10-01');
  const lastDate = Date.parse('2026-11-30');
  const state = { lang: 'en', page: 'home', view: 'week', zone: 'local', date: null, event: null, track: 'offline', metric: 'chips', scores: true, desktop: null, summaryDate: null };
  const boardDates = { offline: 'all', online: 'all' };
  const scoreOverrides = new Set();

  const copy = {
    en: {
      preview: 'DEMO', brandDescription: 'Fictional prediction arena', calendar: 'Calendar', home: 'Leaderboard', stats: 'Stats', desks: 'Snapshots',
      season: 'Example season', calendarTitle: 'Match Calendar', today: 'Today', week: 'Week', month: 'Month',
      previous: 'Previous', next: 'Next', timezone: 'Time zone', local: 'Browser local time',
      scheduled: 'Scheduled', live: 'Live', done: 'Finished', postponed: 'Postponed', canceled: 'Canceled', tbd: 'TBD', noMatches: 'No matches scheduled',
      matchDay: 'Match Day', current: 'Current match day', latest: 'Latest match day', upcoming: 'Next match day',
      offline: 'Offline', online: 'Online', playin: 'Play-ins', swiss: 'Swiss', quarter: 'Quarterfinal', semi: 'Semifinal', final: 'Final',
      show: 'Reveal scores', hide: 'Hide scores', matchScore: 'Show match score', leaderboard: 'Model leaderboard', chips: 'Chips', accuracy: 'Accuracy',
      rank: 'Rank', model: 'Model / Harness', balance: 'Settled chips', hits: 'Hits / scored', usage: 'Token and cost', observed: 'Observed tokens',
      billed: 'Billed', estimated: 'Official API estimate', coverage: 'Token coverage', phase: 'Phase', initial: 'Init',
      recovery: 'Recovery', review: 'Online review', snapshot: 'Read-only snapshots',
      unavailable: 'No data yet', statsCopy: 'Arena usage', arena: 'Arena', loading: 'Loading Worlds 2026…',
      fetchError: 'Unable to load the local data snapshot.', refreshed: 'Snapshot updated', opggSource: 'OP.GG match',
      enabled: 'Enabled in config', disabled: 'Disabled in config', human: 'Human participant',
      batch: 'Planned Arena timeline', lock: 'Lock', offlineStart: 'Offline start', onlineStart: 'Online start',
      monthNav: 'Mini calendar', dateRange: 'Calendar period', views: 'Calendar view', openCalendar: 'Open in calendar',
      plannedDuration: 'Planned duration',
      dayAfter: '+1 day', dayBefore: '−1 day', language: 'Language', languages: 'Available languages',
      replay: 'Retrospective offline replay', settledDays: 'Settled days',
      allSettled: 'All settled days', cumulative: 'Cumulative settlement', throughDate: 'Settlement through date',
      participants: 'Models', accepted: 'Accepted predictions', direction: 'Winner direction', neutral: 'Neutral',
      abstain: 'Abstained', brier: 'Mean Brier', chart: 'Settled chips by model', attempts: 'Attempts',
      chartBaseline: 'Starting chips at center', loansTaken: 'Loans taken',
      duration: 'Wall time', completion: 'Ended', verified: 'Verified', unverified: 'Unverified',
      summary: 'Summary', written: 'Written', missing: 'Missing', exitCode: 'Exit',
      usageNote: 'Usage comes from completed native traces. Cost totals cover priced attempts using fixed official API rates in USD. Accepted predictions are cumulative for the model within the day.',
      runtimeScope: 'Completed attempts across match days', recovery1: 'Recovery 1', recovery2: 'Recovery 2',
      attemptCoverage: 'Attempt records', costCoverage: 'Cost coverage',
      capture: 'Capture screenshot', captureLoading: 'Capturing…', captureError: 'Desktop unavailable. Try again.',
      captureReady: 'Screenshot captured', selectDesktop: 'Model desktop',
      desktopNote: 'Capture the current desktop for the selected model.',
      desktopAccess: 'Latest reviewed screenshot for the selected desktop.',
      publishedSnapshot: 'Published snapshot', noScreenshot: 'No screenshot has been published for this desktop.',
      dailySummaries: 'Daily summaries', matchDate: 'Match date', noSummaries: 'No daily summary has been published for this model and track.',
      loadingSummary: 'Loading summary…', summaryError: 'Unable to load this summary.',
      onlinePending: 'No online results in this preview.', unavailableSnapshot: 'Arena data is unavailable. Reload to retry.',
      latestBatch: 'Latest batch', refreshArena: 'Refresh arena data', windowEnds: 'Window ends',
      prepared: 'Prepared', running: 'Running', locked: 'Locked', settled: 'Settled', pending: 'Pending',
      activeAgents: 'Agents running', autoRefresh: 'Refreshes every 5 min', noRecovery: 'Not run',
      bankReceived: 'Received by Bank', bankProgress: 'Prediction contents stay sealed in this view.',
    },
    zh: {
      preview: '演示', brandDescription: '虚构赛事预测竞技场', calendar: '赛历', home: '榜单', stats: '统计', desks: '快照',
      season: '演示赛季', calendarTitle: '比赛赛历', today: '今天', week: '周', month: '月',
      previous: '上一页', next: '下一页', timezone: '时区', local: '浏览器本地时间',
      scheduled: '已排期', live: '进行中', done: '已完赛', postponed: '已延期', canceled: '已取消', tbd: '待定', noMatches: '暂无比赛安排',
      matchDay: '比赛日', current: '当前比赛日', latest: '最近比赛日', upcoming: '下一比赛日',
      offline: '离线', online: '联网', playin: '入围赛', swiss: '瑞士轮', quarter: '四分之一决赛', semi: '半决赛', final: '决赛',
      show: '显示比分', hide: '隐藏比分', matchScore: '显示本场比分', leaderboard: '模型榜单', chips: '筹码榜', accuracy: '准确榜',
      rank: '排名', model: '模型 / Harness', balance: '结算筹码', hits: '命中 / 计分', usage: 'Token 与费用', observed: '已观测 Token',
      billed: '实计费用', estimated: '官方 API 估算', coverage: 'Token 覆盖率', phase: '阶段', initial: '首轮（Init）',
      recovery: '恢复', review: '联网复核', snapshot: '只读快照',
      unavailable: '暂无', statsCopy: 'Arena 用量', arena: 'Arena', loading: '正在加载 2026 全球总决赛…',
      fetchError: '无法加载本地数据快照。', refreshed: '快照更新于', opggSource: 'OP.GG 场次',
      enabled: '配置已启用', disabled: '配置未启用', human: '真人参赛者',
      batch: 'Arena 计划时间线', lock: '封盘', offlineStart: '离线首轮', onlineStart: '联网首轮',
      monthNav: '迷你月历', dateRange: '赛历日期范围', views: '赛历视图', openCalendar: '在赛历中打开',
      plannedDuration: '预估时长',
      dayAfter: '+1 天', dayBefore: '−1 天', language: '语言', languages: '可用语言',
      replay: '回顾性离线回放', settledDays: '已结算比赛日',
      allSettled: '全部已结算日', cumulative: '累计结算', throughDate: '截至当日的结算',
      participants: '模型数', accepted: '已接收预测', direction: '胜负方向', neutral: '中性',
      abstain: '放弃', brier: '平均 Brier', chart: '各模型结算筹码', attempts: '运行次数',
      chartBaseline: '中线为起始筹码', loansTaken: '贷款次数',
      duration: '实际用时', completion: '结束时间', verified: '已核验', unverified: '待核验',
      summary: '日档', written: '已写', missing: '缺失', exitCode: '退出码',
      usageNote: 'Token 来自已结束的原生运行轨迹。费用仅汇总可计价的尝试，按固定版本的官方 API 价格估算，单位 USD。已接收预测按模型在当日累计。',
      runtimeScope: '各比赛日累计已结束尝试', recovery1: '恢复 1', recovery2: '恢复 2',
      attemptCoverage: '尝试记录覆盖', costCoverage: '费用覆盖率',
      capture: '截取桌面', captureLoading: '正在截图…', captureError: '无法获取桌面，请重试。',
      captureReady: '截图已获取', selectDesktop: '模型桌面',
      desktopNote: '按需截取所选模型的当前桌面。',
      desktopAccess: '显示所选桌面最近一次审核通过的截图。',
      publishedSnapshot: '已发布快照', noScreenshot: '该桌面暂无已发布截图。',
      dailySummaries: '每日日档', matchDate: '比赛日', noSummaries: '该模型在此赛道暂无已发布日档。',
      loadingSummary: '正在加载日档…', summaryError: '无法加载此日档。',
      onlinePending: '预览暂无联网赛道结果。', unavailableSnapshot: '竞技场数据暂不可用，请重新加载。',
      latestBatch: '最新批次', refreshArena: '刷新竞技场数据', windowEnds: '窗口截止',
      prepared: '已准备', running: '运行中', locked: '已封盘', settled: '已结算', pending: '待运行',
      activeAgents: 'Agent 运行中', autoRefresh: '每 5 分钟自动刷新', noRecovery: '未运行',
      bankReceived: 'Bank 已接收', bankProgress: '此视图只显示接收进度，预测内容保持封存。',
    }
  };

  const regions = [
    ['America/Los_Angeles', 'Pacific', '美西'],
    ['America/Chicago', 'US Central', '美中'],
    ['America/Sao_Paulo', 'São Paulo', '圣保罗'],
    ['Europe/Paris', 'Paris', '巴黎'],
    ['Asia/Shanghai', 'Beijing', '北京'],
    ['Asia/Seoul', 'Seoul', '首尔']
  ];
  let siteData = null;
  let events = [];
  let arenaFailed = false;
  let arenaRequest = null;

  const preferencesKey = 'rift-forecast.preferences.v1';

  function readPreferences() {
    try {
      return JSON.parse(localStorage.getItem(preferencesKey));
    } catch {
      return null;
    }
  }
  function restorePreferences(settings) {
    if (!settings) return;
    if (['en', 'zh'].includes(settings.lang)) state.lang = settings.lang;
    if (['local', 'UTC', ...regions.map(([zone]) => zone)].includes(settings.zone)) state.zone = settings.zone;
  }
  restorePreferences(readPreferences());

  const t = (key) => copy[state.lang][key];
  const escape = (value) => String(value).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
  const locale = () => state.lang === 'zh' ? 'zh-CN' : 'en-US';
  const mainZone = () => state.zone === 'local' ? localZone : state.zone;
  const regionName = (region) => region[state.lang === 'zh' ? 2 : 1];
  const zoneName = (zone) => {
    const region = regions.find(([id]) => id === zone);
    return region ? regionName(region) : zone;
  };
  const asDate = (epoch) => new Date(epoch);
  const dayNumber = (epoch) => asDate(epoch).getUTCDate();
  const monthNumber = (epoch) => asDate(epoch).getUTCMonth();
  const startOfMonth = (epoch, shift = 0) => Date.UTC(asDate(epoch).getUTCFullYear(), monthNumber(epoch) + shift, 1);
  const startOfWeek = (epoch) => epoch - asDate(epoch).getUTCDay() * DAY;
  const week = (epoch) => Array.from({ length: 7 }, (_, index) => startOfWeek(epoch) + index * DAY);
  const dateFormat = (epoch, opts) => new Intl.DateTimeFormat(locale(), { timeZone: 'UTC', ...opts }).format(epoch);
  const fullDate = (epoch) => dateFormat(epoch, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
  const shortDate = (epoch) => dateFormat(epoch, { month: 'short', day: 'numeric' });
  const monthLabel = (epoch) => dateFormat(epoch, { month: 'long', year: 'numeric' });
  const dayLabel = (epoch) => dateFormat(epoch, { weekday: 'short' });
  const startLabel = (hour, minute = 0) => String(hour).padStart(2, '0') + ':' + String(minute).padStart(2, '0');

  const clockFormatters = new Map();
  function clock(epoch, zone = mainZone()) {
    if (!clockFormatters.has(zone)) {
      clockFormatters.set(zone, new Intl.DateTimeFormat('en-GB', {
        timeZone: zone, year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
        hourCycle: 'h23', timeZoneName: 'shortOffset'
      }));
    }
    const parts = Object.fromEntries(clockFormatters.get(zone).formatToParts(epoch).map(({type, value}) => [type, value]));
    return { date: Date.UTC(+parts.year, +parts.month - 1, +parts.day), hour: +parts.hour, minute: +parts.minute, offset: parts.timeZoneName.replace('GMT', 'UTC') };
  }
  function eventMap() {
    const days = new Map();
    for (const event of events) {
      const wall = clock(event.time);
      const dayEvents = days.get(wall.date) || [];
      dayEvents.push({ ...event, wall });
      days.set(wall.date, dayEvents);
    }
    return days;
  }
  const displayDate = () => state.date ?? Math.min(lastDate, Math.max(firstDate, clock(snapshot).date));
  const monthDays = (date) => {
    const start = startOfWeek(startOfMonth(date));
    const end = startOfWeek(startOfMonth(date, 1) - DAY) + 7 * DAY;
    return Array.from({ length: (end - start) / DAY }, (_, index) => start + index * DAY);
  };

  const button = (label, action, value, extra = '') =>
    `<button type="button" class="av-btn" data-action="${action}" data-value="${value}" ${extra}>${label}</button>`;
  const pressed = (on) => `aria-pressed="${on}"`;
  const isoTime = (event) => asDate(event.time).toISOString();

  function appHeading(title, right = '', kicker = t('season')) {
    return `<div class="av-head">
      <div class="av-hero-art" aria-hidden="true"><svg><use href="#rift-mark"></use></svg></div>
      <div class="av-between av-hero-content"><div><p class="av-kicker">${kicker}</p><h2>${title}</h2></div>${right}</div>
    </div>`;
  }

  function timeZones() {
    const choices = [['local', `${t('local')} · ${zoneName(localZone)}`], ...regions.map((region) => [region[0], regionName(region)]), ['UTC', 'UTC']];
    return `<label class="av-row av-zone"><span>${t('timezone')}</span><select data-timezone>${choices.map(([zone, name]) =>
      `<option value="${zone}" ${zone === state.zone ? 'selected' : ''}>${name}</option>`).join('')}</select></label>`;
  }

  function alternateTimes(event, local) {
    return regions.filter(([id]) => id !== mainZone()).map(([id, ...names]) => {
      const at = clock(event.time, id);
      const delta = (at.date - local.date) / DAY;
      const mark = delta ? `<sup aria-label="${fullDate(at.date)}"> ${t(delta > 0 ? 'dayAfter' : 'dayBefore')}</sup>` : '';
      return `<span>${startLabel(at.hour, at.minute)}${mark} ${regionName([id, ...names])}</span>`;
    }).join('');
  }

  const visibleScore = (event) => event.score && state.scores !== scoreOverrides.has(event.id)
    ? event.score.join('–') : null;

  function eventName(event) {
    const names = event.teams.map((code) => escape(code === 'TBD' ? t('tbd') : code));
    return names.join(' ' + (visibleScore(event) || 'vs') + ' ');
  }

  function matchup(event) {
    const team = (code, side) => {
      const info = siteData.teams[code] || { name: code, logoInsets: [0, 0] };
      const name = code === 'TBD' ? t('tbd') : code;
      const [start, end] = info.logoInsets ?? [0, 0];
      return `<span class="av-team av-team-${side}" title="${escape(info.name)}"><img src="${escape(new URL('assets/neutral.svg', assetBase))}" alt="" width="40" height="40" style="--logo-inset-start:${Number(start)};--logo-inset-end:${Number(end)}" ${code === 'TBD' ? 'data-placeholder' : ''}>${escape(name)}</span>`;
    };
    const [left, right] = event.teams;
    const score = visibleScore(event);
    const badge = `<span class="av-versus-value">${score || 'vs'}</span>`;
    const center = event.score
      ? `<button type="button" class="av-versus${score ? ' av-score' : ''}" data-action="matchScore" data-value="${escape(event.id)}"
          aria-label="${t('matchScore')} · ${escape(left)} vs ${escape(right)}${score ? ' · ' + score : ''}" ${pressed(Boolean(score))}>${badge}</button>`
      : `<span class="av-versus">${badge}</span>`;
    return `<p class="av-match-title av-matchup">
      ${team(left, 'left')}
      ${center}
      ${team(right, 'right')}
    </p>`;
  }

  function plannedDuration(event) {
    const hours = new Intl.NumberFormat(locale(), { style: 'unit', unit: 'hour', unitDisplay: 'short' });
    return `${t('plannedDuration')} ${hours.format(event.bestOf)}`;
  }

  const formatBadge = (event) => `<span class="av-format" data-format="${event.bestOf}">${event.format}</span>`;

  function eventCard(event, showDuration = false) {
    const wall = clock(event.time);
    const secondary = alternateTimes(event, wall);
    const opgg = event.status !== 'tbd' && /^https:\/\/esports\.op\.gg\/matches\/[0-9]+$/.test(event.source);
    return `<article class="av-match">
      <div class="av-between"><span class="av-meta">${t(event.stage)} · ${showDuration ? formatBadge(event) + ' · ' + plannedDuration(event) : event.format}</span><span class="av-status ${event.status}">${t(event.status)}</span></div>
      ${matchup(event)}
      <div class="av-local-time"><time datetime="${isoTime(event)}">${shortDate(wall.date)} · ${startLabel(wall.hour, wall.minute)}</time> <span class="av-meta">${zoneName(mainZone())} (${wall.offset})</span></div>
      <div class="av-time" aria-label="${t('timezone')}">${secondary}</div>
      <p class="av-meta">${t('arena')} · ${event.arena ? Object.entries(event.arena).map(([track, batch]) =>
        `${t(track)} ${t(batch.state)}`).join(' · ') : t('unavailable')}${opgg
        ? ` · <a href="${escape(event.source)}" target="_blank" rel="noopener noreferrer">${t('opggSource')}</a>` : ''}</p>
    </article>`;
  }

  function miniCalendar(date, days) {
    const month = monthNumber(date);
    return `<aside class="av-rail" aria-label="${t('monthNav')}">
      <div class="av-between"><h4>${monthLabel(date)}</h4>
        <div class="av-row">${[-1, 1].map((direction) => {
          const target = startOfMonth(date, direction);
          const disabled = target < firstDate || target > lastDate ? ' disabled' : '';
          return button(direction < 0 ? '‹' : '›', 'miniMonth', direction,
            'aria-label="' + t(direction < 0 ? 'previous' : 'next') + ' ' + t('month') + '"' + disabled);
        }).join('')}</div>
      </div>
      <div class="av-mini">
        ${week(date).map((epoch) => `<span class="av-meta">${dayLabel(epoch).slice(0, state.lang === 'zh' ? 2 : 1)}</span>`).join('')}
        ${monthDays(date).map((epoch) => `<button type="button" class="${monthNumber(epoch) === month ? '' : 'av-out'}" data-action="date" data-value="${epoch}" ${pressed(epoch === date)} aria-label="${fullDate(epoch)}" ${days.has(epoch) ? 'data-event-day' : ''} ${epoch < firstDate || epoch > lastDate ? 'disabled' : ''}>${dayNumber(epoch)}</button>`).join('')}
      </div>
      <div class="av-legend av-meta">${['done', 'scheduled', 'tbd'].map((status) => `<span><i class="av-dot ${status}" aria-hidden="true"></i>${t(status)}</span>`).join('')}</div>
    </aside>`;
  }

  function dayButton(epoch, date, today) {
    return `<button type="button" class="av-day-label" data-action="date" data-value="${epoch}" ${pressed(epoch === date)} ${epoch === today ? 'aria-current="date"' : ''} aria-label="${fullDate(epoch)}" ${epoch < firstDate || epoch > lastDate ? 'disabled' : ''}><span class="av-meta">${dayLabel(epoch)}</span><strong>${dayNumber(epoch)}</strong></button>`;
  }

  const minuteOfDay = (wall) => wall.hour * 60 + wall.minute;
  const minuteLabel = (minute) => startLabel(Math.floor(minute / 60), minute % 60);

  function timelineBlocks(date, days) {
    const blocks = [];
    for (const startDate of [date - DAY, date]) {
      for (const event of days.get(startDate) || []) {
        const start = minuteOfDay(event.wall) + (startDate - date) / MINUTE;
        const end = start + event.bestOf * 60;
        if (end > 0) blocks.push({ event, start: Math.max(0, start), end: Math.min(MINUTES_PER_DAY, end) });
      }
    }
    blocks.sort((a, b) => a.start - b.start || a.event.time - b.event.time);
    let group = { end: 0, lanes: [] };
    for (const block of blocks) {
      if (block.start >= group.end) group = { end: 0, lanes: [] };
      const freeLane = group.lanes.findIndex((end) => end <= block.start);
      block.lane = freeLane < 0 ? group.lanes.length : freeLane;
      group.lanes[block.lane] = block.end;
      group.end = Math.max(group.end, block.end);
      block.group = group;
    }
    return blocks;
  }

  function timeline(dates, active, days, classes = '') {
    const now = clock(snapshot);
    const columns = dates.map((date) => timelineBlocks(date, days));
    const blocks = columns.flat();
    const first = blocks.length ? Math.max(0, Math.floor(Math.min(...blocks.map((block) => block.start)) / 60) - 1) : 0;
    const last = blocks.length ? Math.min(24, Math.max(first + 8, Math.ceil(Math.max(...blocks.map((block) => block.end)) / 60))) : 8;
    const axisStart = Math.min(first, last - 8);
    const hourSize = 47;
    const position = (minute) => (minute / 60 - axisStart) * hourSize;
    const axis = Array.from({ length: last - axisStart }, (_, index) => `<span class="av-hour" style="top:${index * hourSize + 4}px">${startLabel(axisStart + index)}</span>`).join('');
    const blockButton = ({ event, start, end, lane, group }, date) => {
      const width = 100 / group.lanes.length;
      const height = Math.max(1, position(end) - position(start) - 4);
      const time = `${minuteLabel(start)}–${minuteLabel(end)}`;
      const name = eventName(event);
      return `<button type="button" class="av-event ${event.status}"
        style="top:${position(start) + 2}px;height:${height}px;left:calc(${lane * width}% + 2px);width:calc(${width}% - 4px)"
        data-action="event" data-value="${event.id}" data-date="${date}" ${pressed(state.event === event.id)}
        aria-label="${fullDate(date)} · ${time} · ${name} · ${event.format} · ${plannedDuration(event)} · ${t(event.status)}">
        <small class="av-event-meta">${formatBadge(event)}<span>${minuteLabel(start)}</span></small><strong>${name}</strong></button>`;
    };
    return `<div class="${classes}" style="--columns:${dates.length};--hour-size:${hourSize}px;--timeline-height:${(last - axisStart) * hourSize}px">
      <div class="av-days"><span class="av-axis-cap">${now.offset}</span>${dates.map((date) => dayButton(date, active, now.date)).join('')}</div>
      <div class="av-clock-grid"><div class="av-axis" aria-hidden="true">${axis}</div>
        ${dates.map((date, index) => `<div class="av-column" ${date === active ? 'data-selected' : ''}>
          ${columns[index].map((block) => blockButton(block, date)).join('')}
          ${date === now.date && now.hour >= axisStart && now.hour < last ? `<span class="av-now" style="top:${position(minuteOfDay(now))}px" aria-hidden="true"></span>` : ''}
        </div>`).join('')}
      </div></div>`;
  }

  function monthView(date, days) {
    return `<div class="av-month-grid">
      ${week(date).map((epoch) => `<span class="av-meta">${dayLabel(epoch)}</span>`).join('')}
      ${monthDays(date).map((epoch) => {
        const count = days.get(epoch)?.length ?? 0;
        return `<button type="button" class="av-month-day ${monthNumber(epoch) === monthNumber(date) ? '' : 'av-out'}" data-action="date" data-value="${epoch}" ${pressed(epoch === date)} aria-label="${fullDate(epoch)} · ${count}" ${epoch < firstDate || epoch > lastDate ? 'disabled' : ''}><strong>${dayNumber(epoch)}</strong>${count ? `<span class="av-count">● ${count}</span>` : ''}</button>`;
      }).join('')}</div>`;
  }

  function arenaTimeline(event) {
    if (!event || event.status === 'done') return '';
    const anchor = event.arena?.online?.anchor || event.arena?.offline?.anchor;
    const first = anchor ? Date.parse(anchor) : events.find((item) => item.batch === event.batch).time;
    const windows = [['offlineStart', 360], ['onlineStart', 300], ['review', 180], ['lock', 10]];
    return `<details class="av-spacer"><summary>${t('batch')}</summary><table class="av-table"><tbody>${windows.map(([phase, delta]) => {
      const instant = first - delta * MINUTE;
      const local = clock(instant);
      return `<tr><th scope="row">${t(phase)}</th><td><time datetime="${asDate(instant).toISOString()}">${shortDate(local.date)} · ${startLabel(local.hour, local.minute)}</time></td></tr>`;
    }).join('')}</tbody></table></details>`;
  }

  function calendar(days) {
    const date = displayDate();
    const dates = week(date);
    const range = state.view === 'month' ? monthLabel(date)
      : `${shortDate(dates[0])} – ${shortDate(dates[6])}, ${asDate(date).getUTCFullYear()}`;
    const shown = days.get(date) || [];
    const selected = events.find((event) => event.id === state.event);
    const dayContent = selected ? [selected] : shown;
    const prev = shiftDate(date, -1);
    const next = shiftDate(date, 1);
    const paging = direction => `aria-label="${t(direction < 0 ? 'previous' : 'next')} ${t(state.view)}"`;
    let grid;
    if (state.view === 'month') grid = monthView(date, days);
    else grid = timeline(dates, date, days, 'av-desktop-week') +
      `<div class="av-mobile-week"><div class="av-week-strip">${dates.map((epoch) => dayButton(epoch, date, clock(snapshot).date)).join('')}</div>${timeline([date], date, days)}</div>`;
    return appHeading(t('calendarTitle'), timeZones()) +
      `<div class="av-cal">${miniCalendar(date, days)}
        <section class="av-stage">
          <div class="av-between av-toolbar">
            <div class="av-row" aria-label="${t('dateRange')}">
              ${button(t('today'), 'today', '')}
              ${button('‹', 'shift', '-1', paging(-1) + (prev < firstDate ? ' disabled' : ''))}
              ${button('›', 'shift', '1', paging(1) + (next > lastDate ? ' disabled' : ''))}
              <span class="av-range" aria-live="polite">${range}</span>
            </div>
            <div class="av-row" role="group" aria-label="${t('views')}">${['week', 'month'].map((key) => button(t(key), 'view', key, pressed(state.view === key))).join('')}</div>
          </div>
          <div class="av-schedule-grid">${grid}</div>
          <section class="av-detail" aria-label="${fullDate(date)}" aria-live="polite">
            <div class="av-between"><h3>${fullDate(date)}</h3>${spoilerButton()}</div>
            ${dayContent.length ? dayContent.map((event) => eventCard(event, true)).join('') + arenaTimeline(dayContent[0]) : `<p class="av-meta av-spacer">${t('noMatches')}</p>`}
          </section>
        </section>
      </div>`;
  }

  function tracks() {
    return `<div class="av-row" role="group" aria-label="${t('offline')} / ${t('online')}">${['offline', 'online'].map((key) => button(t(key), 'track', key, pressed(state.track === key))).join('')}</div>`;
  }

  function table(headers, rows) {
    return `<table class="av-table"><thead><tr>${headers.map((header) => `<th scope="col">${header}</th>`).join('')}</tr></thead>
      <tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  }

  const metricCard = (label, value) => `<div class="av-panel"><p class="av-meta">${label}</p><strong class="av-big">${value}</strong></div>`;
  const money = (value, digits = 4) => value == null ? '—' : new Intl.NumberFormat(locale(), {
    style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: digits
  }).format(value);
  const chipsValue = (value) => new Intl.NumberFormat(locale(), { maximumFractionDigits: 2 }).format(value);
  const balanceCell = (row) => `${chipsValue(row.balance)}${row.loans_taken ? ` <span class="av-loan-count"
    title="${t('loansTaken')}" aria-label="${t('loansTaken')} · ${row.loans_taken}">(${row.loans_taken})</span>` : ''}`;
  const desktopAccount = (id) => siteData.desktopAccounts?.[state.track]?.[id] || id;
  function modelInfo(id) {
    const account = desktopAccount(id);
    return siteData.models.find((model) => model.id === account);
  }
  const runtimePhaseLabels = { first: 'initial', 'first-1': 'initial', 'recovery-1': 'recovery1',
    'recovery-2': 'recovery2', review: 'review', 'review-1': 'review' };
  function modelCell(id) {
    const info = modelInfo(id);
    return `<span class="av-model-name">${modelIcon(info)}<span class="av-model-text">
      <strong>${escape(info?.displayName || desktopAccount(id))}</strong>
      <span class="av-meta">${escape(participantRole(info))}</span>
    </span></span>`;
  }
  const directions = (row) => `${row.winner_hits} / ${row.n_directional}${row.n_neutral ? ` <small class="av-meta">· ${row.n_neutral} ${t('neutral')}</small>` : ''}`;
  const trackEmpty = () => `<p class="av-meta av-empty-state">${t(arenaFailed ? 'unavailableSnapshot' : state.track === 'online' ? 'onlinePending' : 'unavailable')}</p>`;

  function performanceChart(view) {
    const rows = view.rankings?.chips || [];
    if (!rows.length || !Number.isFinite(view.initial)) return '';
    const sum = (key) => rows.reduce((total, row) => total + row[key], 0);
    const scale = Math.max(1, ...rows.map((row) => Math.abs(row.balance - view.initial)));
    const n = sum('n_settled');
    const slots = n + sum('n_abstain') + sum('n_unknown');
    const brier = n ? rows.reduce((total, row) => total + row.brier * row.n_settled, 0) / n : null;
    const chart = rows.map((row) => {
      const delta = row.balance - view.initial;
      const width = Math.abs(delta) * 50 / scale;
      return `<li>
        <span class="av-profit-name">${modelName(modelInfo(row.model), row.model)}</span>
        <span class="av-profit-axis" aria-hidden="true">
          <span class="av-profit-bar ${delta >= 0 ? 'gain' : 'loss'}" style="--bar-width: ${width}%"></span>
        </span>
        <strong>${balanceCell(row)}</strong>
      </li>`;
    }).join('');
    return `<section class="av-panel av-section av-opening" aria-labelledby="av-performance-heading">
      <div class="av-between"><div><p class="av-meta">${state.track === 'offline' ? t('replay') : t('online')} · ${view.line}</p>
      <h3 id="av-performance-heading">${t(view.date === 'all' ? 'cumulative' : 'throughDate')}</h3></div>
      <span class="av-meta">${escape(view.date === 'all' ? t('allSettled') : view.date)}</span></div>
      <div class="av-meters av-spacer">
        ${metricCard(t('participants'), rows.length)}
        ${metricCard(t('accepted'), `${n} / ${slots}`)}
        ${metricCard(t('direction'), `${sum('winner_hits')} / ${sum('n_directional')} <small class="av-meta">· ${sum('n_neutral')} ${t('neutral')}</small>`)}
        ${metricCard(t('brier'), brier?.toFixed(4) ?? '—')}
      </div>
      <h4 class="av-spacer">${t('chart')}</h4>
      <p class="av-meta">${t('chartBaseline')} · ${chipsValue(view.initial)}</p>
      <ul class="av-profit-list">${chart}</ul>
    </section>`;
  }

  function leaderboard(view) {
    const chips = state.metric === 'chips';
    const models = view.rankings?.[state.metric] || [];
    const rows = models.filter((row) => row.model.toLowerCase() !== 'human').map((row, index) => [
      index + 1, modelCell(row.model),
      balanceCell(row), row.brier?.toFixed(4) ?? '—', directions(row),
      row.n_settled, row.n_abstain, row.n_unknown
    ]);
    return `<section class="av-panel av-section" data-leaderboard>
      <h3>${t('leaderboard')}</h3>
      ${rows.length ? `<p class="av-meta av-spacer">${view.line} · ${view.date === 'all' ? `${t('settledDays')} ${view.count}` : escape(view.date)}</p>
      <div class="av-table-scroll">${table([t('rank'), t('model'), `${t('balance')}${chips ? ' ↓' : ''}`, `Brier${chips ? '' : ' ↑'}`,
        t('direction'), t('accepted'), t('abstain'), 'U'], rows)}</div>` : trackEmpty()}
    </section>`;
  }

  function leaderboardOverview() {
    const board = siteData.leaderboard;
    const daily = board?.daily?.[state.track] || {};
    const dates = Object.keys(daily).sort().reverse();
    const date = dates.includes(boardDates[state.track]) ? boardDates[state.track] : 'all';
    const view = {
      date, rankings: date === 'all' ? board?.tracks?.[state.track] : daily[date],
      line: state.track === 'online' ? 'online-main-v1' : 'main-v1',
      initial: board?.initialChipsByTrack?.[state.track],
      count: board?.settledDaysByTrack?.[state.track] ?? dates.length,
    };
    const datePicker = dates.length ? choicePicker({
      kind: 'leaderboard-date', label: t('matchDate'), action: 'leaderboardDate', selected: date,
      icon: calendarPickerIcon, choices: ['all', ...dates].map((value) => {
        const name = value === 'all' ? t('allSettled') : value;
        return { value, name, content: escape(name) };
      }),
    }) : '';
    return `<div class="av-row av-board-toolbar">${tracks()}
      <div class="av-row" role="group" aria-label="${t('leaderboard')}">${['chips', 'accuracy'].map((key) =>
        button(t(key), 'metric', key, pressed(state.metric === key))).join('')}</div>${datePicker}
    </div>${performanceChart(view)}${leaderboard(view)}`;
  }

  const displayValue = (value) => value == null ? t('unavailable') : escape(value.toLocaleString(locale()));
  const emptyState = () => `<p class="av-meta av-empty-state">${t('unavailable')}</p>`;
  const harnessNames = {
    codex: 'Codex', 'claude-code': 'Claude Code', 'gemini-cli': 'Gemini CLI', zcode: 'ZCode',
    'grok-build': 'Grok Build', 'qwen-code': 'Qwen Code', 'kimi-code': 'Kimi Code CLI',
    'deepseek-harness': 'DeepSeek Harness', opencode: 'OpenCode'
  };
  const participantRole = (info) => info?.kind === 'human' ? t('human') : harnessNames[info?.harness] || info?.harness || t('unavailable');
  const modelIcon = (info) => info?.icon
    ? `<img src="${escape(new URL('assets/neutral.svg', assetBase))}" alt="" width="24" height="24">` : '';
  const modelName = (info, id) => `<span class="av-model-name">${modelIcon(info)}${escape(info?.displayName || desktopAccount(id))}</span>`;

  function spoilerButton() {
    const labels = ['show', 'hide'].map((key) => {
      const inactive = key === (state.scores ? 'show' : 'hide');
      return `<span aria-hidden="${inactive}">${t(key)}</span>`;
    }).join('');
    return button(`<span class="av-spoiler-label">${labels}</span>`, 'scores', '', pressed(state.scores));
  }

  function leaderboardPage(days) {
    const today = clock(snapshot).date;
    const keys = [...days.keys()];
    const current = keys.filter((date) => date <= today).at(-1);
    const upcoming = keys.find((date) => date > today);
    const block = (title, date) => `<section class="av-panel av-matchday-card"><div class="av-between"><h4>${title}</h4>
      ${date === undefined ? '' : `<span class="av-meta">${shortDate(date)}</span>`}</div>
      ${date === undefined ? emptyState() : `<div class="av-spacer">${days.get(date).map((event) => eventCard(event)).join('')}</div>
      <div class="av-spacer">${button(t('openCalendar'), 'openDate', date)}</div>`}</section>`;
    return appHeading(t('home')) + `<div class="av-arena-overview">${leaderboardOverview()}</div>` +
      `<section class="av-section" aria-labelledby="match-day-heading">
        <div class="av-between">
          <h3 id="match-day-heading">${t('matchDay')}</h3>
          <div class="av-row">${timeZones()}${spoilerButton()}</div>
        </div>
        <div class="av-match-grid av-spacer">${block(t(current === today ? 'current' : 'latest'), current)}${block(t('upcoming'), upcoming)}</div>
      </section>`;
  }

  function statistics() {
    const heading = appHeading(t('stats'), `<div class="av-row">${tracks()}${button(t('refreshArena'), 'refreshArena', '')}</div>`, t('statsCopy'));
    return heading + `<div data-arena-stats>${statisticsContent()}</div>`;
  }

  function statisticsContent() {
    const attempts = siteData.runtime?.[state.track] || [];
    const expected = siteData.runtime?.expectedByTrack?.[state.track] ?? attempts.length;
    const time = new Intl.DateTimeFormat(locale(), { timeZone: mainZone(), dateStyle: 'short', timeStyle: 'medium' });
    const batch = latestBatch(time);
    if (!expected) return batch || `<section class="av-panel">${trackEmpty()}</section>`;
    const measured = attempts.filter((row) => row.tokens != null);
    const costs = attempts.filter((row) => row.estimated_usd != null);
    const duration = (seconds) => seconds == null ? '—' : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    const online = state.track === 'online';
    return (arenaFailed ? trackEmpty() : '') +
      `<p class="av-meta av-spacer">${t('runtimeScope')} · ${online ? 'online-main-v1' : 'main-v1'}</p>
      <div class="av-meters av-spacer">
        ${metricCard(t('attempts'), expected)}
        ${metricCard(t('recovery'), attempts.filter((row) => row.phase.startsWith('recovery-')).length)}
        ${online ? metricCard(t('review'), attempts.filter((row) => row.phase === 'review').length) : ''}
        ${metricCard(t('observed'), measured.length ? displayValue(measured.reduce((sum, row) => sum + row.tokens, 0)) : '—')}
        ${metricCard(t('estimated'), money(costs.length ? costs.reduce((sum, row) => sum + row.estimated_usd, 0) : null, 2))}
        ${metricCard(t('attemptCoverage'), `${attempts.length} / ${expected}`)}
        ${metricCard(t('coverage'), `${measured.length} / ${expected}`)}
        ${metricCard(t('costCoverage'), `${costs.length} / ${expected}`)}
      </div>` + batch +
      `<section class="av-panel av-section">
        <div class="av-between"><h3>${t('attempts')}</h3><span class="av-meta">${zoneName(mainZone())}</span></div>
        <div class="av-table-scroll">${table([t('matchDate'), t('model'), t('phase'), t('duration'), t('completion'), t('accepted'),
          t('summary'), 'Token', t('estimated')], attempts.map((row) => [
          escape(row.date || '—'), modelCell(desktopAccount(row.model)),
          `${t(runtimePhaseLabels[row.phase] || 'recovery')}<br><span class="av-meta">${t(row.verified ? 'verified' : 'unverified')} · ${t('exitCode')} ${row.exit}</span>`,
          duration(row.duration), row.finished ? time.format(Date.parse(row.finished)) : '—', row.accepted ?? '—',
          t(row.note ? 'written' : 'missing'),
          row.tokens == null ? '—' : displayValue(row.tokens), money(row.estimated_usd)
        ]))}</div>
        <p class="av-meta av-spacer">${t('usageNote')}${siteData.runtime.pricingVersion ? ` · ${escape(siteData.runtime.pricingVersion)}` : ''}</p>
      </section>`;
  }

  const runningBadge = (animated = false) => `<span class="av-run-badge">${animated
    ? '<span class="av-run-spinner" aria-hidden="true"></span>'
    : '<span class="av-run-dot" aria-hidden="true"></span>'}${t('running')}</span>`;

  function latestBatch(time) {
    const batch = siteData.batches?.[state.track];
    if (!batch) return '';
    const status = { pending: 'pending', done: 'done', U: 'unverified' };
    const phase = (value) => value === 'running' ? runningBadge(true) : t(status[value] || 'unavailable');
    const online = state.track === 'online';
    const active = batch.models.filter((row) => [row.first, row.recovery, row.review].includes('running')).length;
    const observed = siteData.arenaObservedAt;
    const rows = batch.models.map((row) => [
      modelCell(row.model), phase(row.first), row.recovery === 'pending' ? t('noRecovery') : phase(row.recovery),
      ...(online ? [phase(row.review ?? 'pending')] : []),
      `${row.accepted} / ${batch.expected}`, row.deadline ? time.format(Date.parse(row.deadline)) : '—'
    ]);
    return `<section class="av-panel av-section av-batch${batch.state === 'running' ? ' av-batch-running' : ''}">
      <div class="av-between">
        <div><p class="av-meta">${online ? 'online-main-v1' : 'main-v1'} · ${shortDate(clock(Date.parse(batch.anchor), mainZone()).date)}</p><h3>${t('latestBatch')}</h3></div>
        <div class="av-row">${batch.state === 'running'
          ? `${runningBadge()} <span class="av-meta av-active-count">${active} / ${batch.models.length} ${t('activeAgents')}</span>`
          : `<strong class="av-meta">${t(batch.state)}</strong>`}</div>
      </div>
      <div class="av-table-scroll">${table([t('model'), t('initial'), t('recovery'),
        ...(online ? [t('review')] : []), t('bankReceived'), t('windowEnds')], rows)}</div>
      <p class="av-meta av-spacer">${t('publishedSnapshot')} <time datetime="${escape(observed)}">${time.format(Date.parse(observed))}</time>
      · ${zoneName(mainZone())}. ${t('bankProgress')}</p>
    </section>`;
  }

  const calendarPickerIcon = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" aria-hidden="true"><rect x="2.5" y="3.5" width="11" height="10" rx="2"/><path d="M5 2v3m6-3v3M3 7h10"/></svg>';

  function choicePicker({ kind, label, action, selected, icon, choices }) {
    const current = choices.find((choice) => choice.value === selected);
    return `<details class="desktop-language-picker av-desktop-picker av-${kind}-picker" data-${kind}-picker>
      <summary class="desktop-language-trigger" aria-label="${label} · ${escape(current.name)}">
        <span class="desktop-language-pill"><span class="av-model-name">${icon}
          <span class="av-picker-label">${escape(current.name)}</span>
        </span>
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" aria-hidden="true"><path d="m4 6 4 4 4-4"/></svg>
        </span>
      </summary>
      <div class="desktop-language-menu" role="group" aria-label="${label}">
        ${choices.map(({ value, content }) => `<button type="button" data-action="${action}" data-value="${escape(value)}" ${pressed(value === selected)}>${content}</button>`).join('')}
      </div>
    </details>`;
  }

  function desktops() {
    const models = siteData.desktops?.[state.track] || [];
    const selected = models.includes(state.desktop) ? state.desktop : models[0];
    const account = desktopAccount(selected);
    const info = modelInfo(account);
    const name = info?.displayName || account;
    const dates = siteData.gallery?.summaries?.[state.track]?.[selected] || [];
    const date = dates.includes(state.summaryDate) ? state.summaryDate : dates[0];
    return appHeading(t('desks'), tracks()) + `<section class="av-panel">
      <h3>${t('snapshot')}</h3><p class="av-meta av-spacer">${t('desktopAccess')}</p>
      ${models.length ? `<div class="av-row av-spacer av-public-desktop-controls">
        ${choicePicker({
          kind: 'model', label: t('selectDesktop'), action: 'desktopModel', selected, icon: modelIcon(info),
          choices: models.map((id) => {
            const model = desktopAccount(id);
            const info = modelInfo(model);
            return { value: id, name: info?.displayName || model, content: modelName(info, model) };
          })
        })}
        ${date ? choicePicker({
          kind: 'date', label: t('matchDate'), action: 'summaryDate', selected: date,
          icon: calendarPickerIcon,
          choices: dates.map((value) => ({ value, name: value, content: escape(value) }))
        }) : ''}
      </div>
      <div class="av-spacer" data-desktop-progress>${desktopProgress(selected)}</div>
      ${publishedScreenshot(selected, name)}` : trackEmpty()}
    </section><section class="av-panel av-section">
      <div class="av-between"><h3>${t('dailySummaries')}</h3><span class="av-meta" data-summary-phase hidden></span></div>
      <p class="av-meta av-spacer" role="status" data-summary-status>${t(dates.length ? 'loadingSummary' : 'noSummaries')}</p>
      <pre class="av-summary-text" data-summary-text hidden></pre>
    </section>`;
  }

  function desktopProgress(model) {
    const batch = siteData.batches?.[state.track];
    const account = desktopAccount(model);
    const row = batch?.models.find((entry) => entry.model === account);
    if (!row) return '';
    const phases = ['review', 'recovery', 'first'];
    const phase = phases.find((key) => row[key] === 'running') ||
      phases.find((key) => ['done', 'U'].includes(row[key])) || 'first';
    const phaseLabel = { first: 'initial', recovery: 'recovery', review: 'review' };
    const status = { pending: 'pending', done: 'done', U: 'unverified' };
    const date = batch.date || new Date(clock(Date.parse(batch.anchor), 'Asia/Shanghai').date).toISOString().slice(0, 10);
    return `<div class="av-row av-meta">${t('arena')} · ${t(state.track)} · ${escape(date)}
      · ${t(batch.state)} · ${t(phaseLabel[phase])}
      ${row[phase] === 'running' ? runningBadge(true) : t(status[row[phase]] || 'unavailable')}
      · ${t('bankReceived')} ${row.accepted} / ${batch.expected}</div>`;
  }

  function publishedScreenshot(model, name) {
    const picture = siteData.gallery?.screenshots?.[state.track]?.[model];
    if (!picture) return `<p class="av-meta av-spacer">${t('noScreenshot')}</p>`;
    const captured = picture.capturedAt || picture;
    const url = picture.webp ? `data:image/webp;base64,${picture.webp}` :
      new URL(`gallery/screenshots/${state.track}/${encodeURIComponent(model)}.webp?v=${encodeURIComponent(captured)}`, gallerySource);
    const time = new Intl.DateTimeFormat(locale(), { timeZone: mainZone(), dateStyle: 'medium', timeStyle: 'short' });
    return `<div class="av-screen">
      <p class="av-meta">${t('publishedSnapshot')} · <time datetime="${escape(captured)}">${time.format(Date.parse(captured))}</time> · ${zoneName(mainZone())}</p>
      <img data-published-screenshot src="${escape(url)}" alt="${t('snapshot')} · ${escape(name)}" width="1280" height="800" decoding="async">
    </div>`;
  }

  async function loadSummary() {
    const option = main.querySelector('[data-date-picker] [aria-pressed="true"]');
    if (!option) return;
    const model = main.querySelector('[data-model-picker] [aria-pressed="true"]').dataset.value;
    const date = option.dataset.value;
    const stillSelected = () => option.isConnected && option.getAttribute('aria-pressed') === 'true';
    const text = main.querySelector('[data-summary-text]');
    const status = main.querySelector('[data-summary-status]');
    const label = main.querySelector('[data-summary-phase]');
    const phase = runtimePhaseLabels[siteData.gallery?.notePhases?.[state.track]?.[model]?.[date]];
    label.hidden = !phase;
    label.textContent = phase ? t(phase) : '';
    const published = siteData.gallery?.notes?.[state.track]?.[model]?.[date];
    if (typeof published === 'string') {
      text.textContent = published;
      text.hidden = false;
      status.hidden = true;
      return;
    }
    const url = new URL(`gallery/summaries/${state.track}/${encodeURIComponent(date)}/${encodeURIComponent(model)}.txt`, gallerySource);
    text.hidden = true;
    status.hidden = false;
    status.textContent = t('loadingSummary');
    try {
      const response = await fetch(url, { cache: 'no-cache', signal: AbortSignal.timeout(15000) });
      if (!response.ok) throw new Error('Summary unavailable');
      const note = await response.text();
      if (!stillSelected()) return;
      text.textContent = note;
      text.hidden = false;
      status.hidden = true;
    } catch {
      if (stillSelected()) status.textContent = t('summaryError');
    }
  }

  function shiftDate(date, amount) {
    if (state.view === 'month') {
      const start = startOfMonth(date, amount);
      return Math.min(start + (dayNumber(date) - 1) * DAY, startOfMonth(start, 1) - DAY);
    }
    return date + amount * 7 * DAY;
  }

  function render(motion) {
    document.documentElement.lang = locale();
    const selected = languageOptions.find(option => option.dataset.language === state.lang);
    languageLabel.textContent = selected.textContent;
    languageTrigger.lang = selected.lang;
    languageTrigger.setAttribute('aria-label', `${t('language')}: ${selected.textContent}`);
    languageMenu.setAttribute('aria-label', t('languages'));
    languageOptions.forEach(option => option.setAttribute('aria-pressed', String(option === selected)));
    root.querySelector('[data-brand-description]').textContent = t('brandDescription');
    root.querySelector('[data-preview]').textContent = t('preview');
    nav.innerHTML = ['home', 'calendar', 'stats', 'desks'].map((key) => button(t(key), 'page', key, key === state.page ? 'aria-current="page"' : '')).join('');
    if (!siteData) {
      main.innerHTML = `<p class="av-panel" role="status">${t(loadFailed ? 'fetchError' : 'loading')}</p>`;
      return;
    }
    const days = eventMap();
    const pages = { calendar: () => calendar(days), home: () => leaderboardPage(days), stats: statistics, desks: desktops };
    main.innerHTML = pages[state.page]();
    if (state.page === 'desks') loadSummary();
    const targets = {
      page: ':scope > *',
      schedule: '.av-schedule-grid',
      detail: '.av-detail .av-match',
      scores: '.av-score .av-versus-value',
      chart: '.av-arena-overview',
      table: '.av-table tbody'
    };
    if (motion) {
      main.querySelectorAll(targets[motion]).forEach((element) => element.classList.add('av-motion-' + motion));
    }
  }

  function savePreferences() {
    try {
      localStorage.setItem(preferencesKey, JSON.stringify({ lang: state.lang, zone: state.zone }));
    } catch {
      // Keep preferences for this visit if storage is unavailable.
    }
  }

  languageOptions.forEach(option => option.addEventListener('click', () => {
    languagePicker.open = false;
    if (state.lang !== option.dataset.language) {
      state.lang = option.dataset.language;
      render();
      savePreferences();
    }
    languageTrigger.focus();
  }));

  document.addEventListener('pointerdown', (event) => {
    for (const picker of root.querySelectorAll('details.desktop-language-picker[open]')) {
      if (!picker.contains(event.target)) picker.open = false;
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      const picker = event.target.closest?.('details.desktop-language-picker[open]') ||
        root.querySelector('details.desktop-language-picker[open]');
      if (!picker) return;
      picker.open = false;
      picker.querySelector('summary').focus();
    }
  });

  root.addEventListener('change', (event) => {
    if (!event.target.matches('[data-timezone]')) return;
    state.zone = event.target.value;
    if (state.event) state.date = clock(events.find((item) => item.id === state.event).time).date;
    render();
    root.querySelector('[data-timezone]').focus({ preventScroll: true });
    savePreferences();
  });

  root.addEventListener('click', (event) => {
    const control = event.target.closest('button[data-action]');
    if (!control) return;
    const { action, value } = control.dataset;
    if (action === 'desktopModel') {
      state.desktop = value;
      render();
      main.querySelector('[data-model-picker] summary').focus({ preventScroll: true });
      return;
    }
    if (action === 'leaderboardDate') {
      boardDates[state.track] = value;
      const overview = main.querySelector('.av-arena-overview');
      overview.innerHTML = leaderboardOverview();
      overview.classList.add('av-motion-chart');
      main.querySelector('[data-leaderboard-date-picker] summary').focus({ preventScroll: true });
      return;
    }
    if (action === 'summaryDate') {
      const picker = control.closest('[data-date-picker]');
      const trigger = picker.querySelector('summary');
      picker.open = false;
      trigger.focus({ preventScroll: true });
      if (control.getAttribute('aria-pressed') === 'true') return;
      state.summaryDate = value;
      picker.querySelector('[aria-pressed="true"]').setAttribute('aria-pressed', 'false');
      control.setAttribute('aria-pressed', 'true');
      trigger.setAttribute('aria-label', `${t('matchDate')} · ${value}`);
      trigger.querySelector('.av-picker-label').textContent = value;
      loadSummary();
      return;
    }
    if (action === 'refreshArena') {
      control.disabled = true;
      loadArena().finally(() => {
        control.disabled = false;
        if (document.activeElement === document.body) control.focus({ preventScroll: true });
      });
      return;
    }
    const previousPage = state.page;
    const mobile = Boolean(control.closest('.av-mobile-week'));
    const rail = Boolean(control.closest('.av-rail'));
    if (action === 'page') state.page = value;
    if (action === 'scores') {
      state.scores = !state.scores;
      scoreOverrides.clear();
    }
    if (action === 'matchScore') {
      if (scoreOverrides.has(value)) scoreOverrides.delete(value);
      else scoreOverrides.add(value);
    }
    if (action === 'track') state.track = value;
    if (action === 'metric') state.metric = value;
    if (action === 'view') state.view = value;
    if (action === 'today') { state.date = null; state.event = null; }
    if (action === 'date' || action === 'openDate') { state.date = Number(value); state.event = null; state.page = 'calendar'; }
    if (action === 'event') {
      const date = Number(control.dataset.date);
      state.event = state.event === value && state.date === date ? null : value;
      state.date = date;
    }
    if (action === 'shift') { state.date = shiftDate(displayDate(), Number(value)); state.event = null; }
    if (action === 'miniMonth') { state.date = startOfMonth(displayDate(), Number(value)); state.event = null; }
    const motion = {
      view: 'schedule', shift: 'schedule', miniMonth: 'schedule', today: 'schedule',
      date: 'detail', event: 'detail', scores: 'scores', track: state.page === 'home' ? 'chart' : 'table', metric: 'table'
    };
    render(previousPage === state.page ? motion[action] : 'page');
    const clickedDate = action === 'event' ? `[data-date="${control.dataset.date}"]` : '';
    const selector = `[data-action="${action}"][data-value="${value}"]${clickedDate}`;
    const scope = mobile ? root.querySelector('.av-mobile-week') : rail ? root.querySelector('.av-rail') : root;
    const replacement = action === 'openDate' ? nav.querySelector('[aria-current="page"]') : scope?.querySelector(selector);
    if (action === 'matchScore' && replacement?.classList.contains('av-score')) {
      replacement.querySelector('.av-versus-value').classList.add('av-motion-scores');
    }
    replacement?.focus({ preventScroll: true });
  });
  let loadFailed = false;
  render();
  const getData = (url, options) => fetch(url, options)
    .then((response) => {
      if (!response.ok) throw new Error(`Snapshot unavailable: ${response.status}`);
      return response.json();
    });
  function loadArena(initial = false) {
    if (!arenaRequest) {
      arenaRequest = refreshArena(initial).finally(() => { arenaRequest = null; });
    }
    return arenaRequest;
  }

  function setCalendar(data, fixtures) {
    const key = (event) => [Date.parse(event.referenceStart || event.start), ...event.teams.slice().sort(), event.bestOf].join('|');
    const statuses = new Map();
    for (const [track, matches] of Object.entries(fixtures || {})) {
      for (const match of matches) {
        const id = key(match);
        statuses.set(id, { ...statuses.get(id), [track]: { state: match.state, anchor: match.anchor } });
      }
    }
    events = data.map((event) => {
      const time = Date.parse(event.start);
      return { ...event, arena: event.arena || statuses.get(key(event)), time,
        batch: clock(time, 'Asia/Shanghai').date, format: 'BO' + event.bestOf };
    });
    if (state.event && !events.some((event) => event.id === state.event)) state.event = null;
  }

  async function refreshArena(initial) {
    const rankingsBefore = JSON.stringify(siteData.leaderboard);
    const failedBefore = arenaFailed;
    const galleryBefore = siteData.gallery?.observedAt;
    const previousPublication = siteData.arenaObservedAt;
    const previousCalendar = siteData.calendarObservedAt;
    let unified = false;
    try {
      const arena = await getData(arenaSource, { cache: 'no-store', signal: AbortSignal.timeout(15000) });
      unified = arena.schemaVersion === 2;
      if (unified && (!Array.isArray(arena.calendar?.events) || !arena.gallery?.notes || !arena.gallery?.screenshots)) {
        throw new Error('Incomplete unified snapshot');
      }
      for (const key of ['leaderboard', 'runtime', 'desktops', 'batches', 'desktopAccounts']) siteData[key] = arena[key];
      if (unified) {
        setCalendar(arena.calendar.events);
        siteData.scheduleSource = arena.calendar.source;
        siteData.calendarObservedAt = arena.calendar.observedAt;
        siteData.gallery = arena.gallery;
      } else {
        throw new Error('Unsupported demo snapshot');
      }
      siteData.arenaObservedAt = arena.observedAt;
      arenaFailed = false;
    } catch {
      arenaFailed = true;
    }
    const updatedPublic = unified && ((previousPublication !== siteData.arenaObservedAt && state.page !== 'stats') ||
      (previousCalendar !== siteData.calendarObservedAt && ['home', 'calendar'].includes(state.page)));
    if (initial || (!arenaFailed && updatedPublic)) {
      render(initial && state.page === 'home' ? 'chart' : undefined);
      return;
    }
    const stats = main.querySelector('[data-arena-stats]');
    if (stats) {
      const scroll = [...stats.querySelectorAll('.av-table-scroll')].map((table) => table.scrollLeft);
      stats.innerHTML = statisticsContent();
      stats.querySelectorAll('.av-table-scroll').forEach((table, index) => { table.scrollLeft = scroll[index] || 0; });
    }
    const rankings = main.querySelector('.av-arena-overview');
    if (rankings && (rankingsBefore !== JSON.stringify(siteData.leaderboard) || failedBefore !== arenaFailed)) {
      const focus = rankings.contains(document.activeElement) ? document.activeElement.dataset : null;
      rankings.innerHTML = leaderboardOverview();
      if (focus) rankings.querySelector(`[data-action="${focus.action}"][data-value="${focus.value}"]`)?.focus({ preventScroll: true });
    }
    if (state.page === 'desks' && (siteData.gallery?.observedAt !== galleryBefore ||
      (!main.querySelector('[data-model-picker]') && siteData.desktops?.[state.track]?.length))) render();
  }
  getData(new URL('data/worlds-2026.json', assetBase))
    .then((data) => {
      setCalendar(data.events);
      siteData = data;
      render('page');
      return loadArena(true).then(() => {
        if (siteData.arenaObservedAt || arenaFailed) setInterval(() => loadArena(), ARENA_REFRESH_INTERVAL);
      });
    })
    .catch(() => {
      siteData = null;
      loadFailed = true;
      render();
    });
})();
