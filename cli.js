/**
 * cli.js — Mini-Jarvis CLI Dashboard
 */

'use strict';

const chalk  = require('chalk');
const rl_mod = require('readline');
const fs     = require('fs');
const path   = require('path');

// ── Constants ───────────────────────────────────────────────────────
const CONTACTS_FILE = path.join(__dirname, 'data', 'contacts.json');
const CONVERSATIONS_DIR = path.join(__dirname, 'data', 'conversations');
const TARGETS_FILE = path.join(__dirname, 'data', 'targets.json');
const ENV_FILE = path.join(__dirname, '.env');
const PERSONALITY_FILE = path.join(__dirname, 'data', 'personality.json');
const MAX_HISTORY = 200;

// ── State ───────────────────────────────────────────────────────────
let rl = null;
let _promptActive = false;
const _startTime = Date.now();
let _verbose = false;
let _quiet   = false;

/** Runtime references — set by init(). */
const _rt = {
    bridgeUrl:     '',
    bridgePort:    5001,
    targetChatIds: [],
    updateTargets: (ids) => {},
    getClient:     () => null,
    httpGet:       null,
    httpPost:      null,
    shutdown:      null,
};

// ── Stats ───────────────────────────────────────────────────────────
const stats = {
    received:   0,
    replied:    0,
    duplicates: 0,
    errors:     0,
    ignored:    0,
};

// ── History buffer ──────────────────────────────────────────────────
const _history = [];

function _push(entry) {
    _history.push({ ...entry, ts: new Date() });
    if (_history.length > MAX_HISTORY) _history.shift();
}

// ═══════════════════════════════════════════════════════════════════
//  HELPERS
// ═══════════════════════════════════════════════════════════════════

const STATE_FILE = path.join(__dirname, 'data', 'state.json');
let _globalState = {
    devmode: false,
    model_enabled: true,
    default_personality: 'jarvis'
};

function loadGlobalState() {
    try {
        if (fs.existsSync(STATE_FILE)) {
            Object.assign(_globalState, JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')));
        }
    } catch {}
}
loadGlobalState();

function saveGlobalState() {
    try { fs.writeFileSync(STATE_FILE, JSON.stringify(_globalState, null, 2)); } catch {}
}

function isModelEnabled() {
    return _globalState.model_enabled;
}

function isTargetEnabled(id) {
    const names = loadTargetNames();
    if (names[id] && names[id].enabled === false) return false;
    return true;
}

function loadTargetNames() {
    try {
        if (!fs.existsSync(TARGETS_FILE)) return {};
        return JSON.parse(fs.readFileSync(TARGETS_FILE, 'utf8'));
    } catch { return {}; }
}

function saveTargetNames(names) {
    fs.writeFileSync(TARGETS_FILE, JSON.stringify(names, null, 2));
}

function updateEnvTargets(newIds) {
    let content = fs.existsSync(ENV_FILE) ? fs.readFileSync(ENV_FILE, 'utf8') : '';
    const newStr = 'TARGET_CHAT_IDS=' + newIds.join(',');
    if (/^TARGET_CHAT_IDS=.*$/m.test(content)) {
        content = content.replace(/^TARGET_CHAT_IDS=.*$/m, newStr);
    } else {
        content += (content.endsWith('\n') || content === '' ? '' : '\n') + newStr + '\n';
    }
    fs.writeFileSync(ENV_FILE, content);
}

function _ts() {
    return chalk.gray(new Date().toLocaleTimeString('en-GB', { hour12: false }));
}

function _uptime() {
    const ms = Date.now() - _startTime;
    const s  = Math.floor(ms / 1000) % 60;
    const m  = Math.floor(ms / 60000) % 60;
    const h  = Math.floor(ms / 3600000);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function _trunc(str, max) {
    return str.length > max ? str.slice(0, max - 1) + '…' : str;
}

function _out(text) {
    if (rl && _promptActive) {
        rl_mod.clearLine(process.stdout, 0);
        rl_mod.cursorTo(process.stdout, 0);
    }
    console.log(text);
    if (rl && _promptActive) {
        rl.prompt(true);
    }
}

function _div(title) {
    const width = 50;
    if (title) {
        const rest = Math.max(0, width - title.length - 4);
        _out(chalk.cyan(`── ${title} ${'─'.repeat(rest)}`));
    } else {
        _out(chalk.cyan('─'.repeat(width)));
    }
}

// ═══════════════════════════════════════════════════════════════════
//  LOGGING FUNCTIONS
// ═══════════════════════════════════════════════════════════════════

function system(msg) { _out(`${_ts()}  ${chalk.cyan('⚙')}  ${msg}`); }
function success(msg) { _out(`${_ts()}  ${chalk.green('✓')}  ${chalk.green(msg)}`); }

function message(sender, chatId, text) {
    stats.received++;
    const names = loadTargetNames();
    const chatName = names[chatId]?.name || chatId;
    _push({ type: 'msg', sender, chat: chatName, text });
    
    const chatPart = (chatId !== sender && chatName) ? chalk.gray(` @ ${chatName}`) : '';
    _out(`${_ts()}  ${chalk.yellow('💬')} ${chalk.bold(sender)}${chatPart}`);
    _out(`            ${chalk.white('"' + _trunc(text, 80) + '"')}`);
}

function reply(text, assistantName = 'AI') {
    stats.replied++;
    _push({ type: 'reply', text });
    _out(`${_ts()}  ${chalk.green('🤖')} ${chalk.bold.green(assistantName)}`);
    _out(`            ${chalk.green('"' + _trunc(text, 80) + '"')}`);
}

function skip(reason) {
    stats.ignored++;
    if (_quiet || !_verbose) return;
    _out(`${_ts()}  ${chalk.dim('⏭  ' + reason)}`);
}

function skipDup(reason) {
    stats.duplicates++;
    if (_quiet || !_verbose) return;
    _out(`${_ts()}  ${chalk.dim('⏭  ' + reason)}`);
}

function warn(msg) { _out(`${_ts()}  ${chalk.yellow('⚠')}  ${chalk.yellow(msg)}`); }

function error(msg, stack) {
    stats.errors++;
    _out(`${_ts()}  ${chalk.red('✗')}  ${chalk.red(msg)}`);
    if (stack && _verbose) {
        for (const line of String(stack).split('\n').slice(0, 5)) {
            _out(chalk.dim(`       ${line.trim()}`));
        }
    }
}

function verbose(msg) {
    if (!_verbose) return;
    _out(`${_ts()}  ${chalk.dim(msg)}`);
}

function bridgePoll(attempt) {
    if (rl && _promptActive) {
        rl_mod.clearLine(process.stdout, 0);
        rl_mod.cursorTo(process.stdout, 0);
    }
    process.stdout.write(chalk.dim(`\r  Attempt ${attempt} — waiting for bridge...`));
}

// ═══════════════════════════════════════════════════════════════════
//  STARTUP BANNER
// ═══════════════════════════════════════════════════════════════════

function banner() {
    const w = 54;
    const h = '─'.repeat(w);
    const centerPad = (text) => {
        const pad = Math.max(0, w - text.length);
        const left = Math.floor(pad / 2);
        return ' '.repeat(left) + text + ' '.repeat(pad - left);
    };
    const leftPad = (text) => text + ' '.repeat(Math.max(0, w - text.length));

    const names = loadTargetNames();

    console.log('');
    console.log(chalk.cyan(`╭${h}╮`));
    console.log(chalk.cyan('│') + chalk.bold(centerPad('🤖 MINI JARVIS — WHATSAPP')) + chalk.cyan('│'));
    console.log(chalk.cyan('│') + chalk.dim(centerPad('PREETAM • QWEN3:8B')) + chalk.cyan('│'));
    console.log(chalk.cyan(`├${h}┤`));
    console.log(chalk.cyan('│') + leftPad(`  💬 Target chats : ${_rt.targetChatIds.length}`) + chalk.cyan('│'));
    for (let i = 0; i < _rt.targetChatIds.length; i++) {
        const id = _rt.targetChatIds[i];
        const tag = id.includes('@g.us') ? chalk.blue('GRP') : chalk.magenta('DM ');
        const name = names[id]?.name || id;
        const disp = name.length > 25 ? name.slice(0, 24) + '…' : name;
        console.log(chalk.cyan('│') + leftPad(`     [${i+1}] ${tag} ${disp}`) + chalk.cyan('│'));
    }
    console.log(chalk.cyan('│') + leftPad(`  🌐 Bridge       : :${_rt.bridgePort}`) + chalk.cyan('│'));
    console.log(chalk.cyan(`╰${h}╯`));
    console.log('');
}

// ═══════════════════════════════════════════════════════════════════
//  COMMAND HANDLERS
// ═══════════════════════════════════════════════════════════════════



async function cmdHumane(args) {
    if (args.length === 0) {
        _out(chalk.dim(`  Usage: /humane <off|on|status> <target>`));
        return;
    }
    const sub = args[0].toLowerCase();
    
    if (sub === 'off' || sub === 'on') {
        if (args.length < 2) return _out(chalk.dim("  Usage: /humane <off|on> <target>"));
        const targetArg = args[1];
        const targets = resolveTargets(targetArg, true);
        if (!targets || targets.length === 0) return _out(chalk.red(`  Invalid target: ${targetArg}`));
        
        let successCount = 0;
        if (_rt.setHumaneMode) {
            for (const chat_id of targets) {
                _rt.setHumaneMode(chat_id, sub);
                successCount++;
            }
            _out(chalk.green(`  ✓ Humane mode set to ${sub.toUpperCase()} for ${successCount} target(s).`));
        } else {
            _out(chalk.red(`  Humane Mode not supported by runtime.`));
        }
    }
    else if (sub === 'status') {
        if (args.length < 2) return _out(chalk.dim("  Usage: /humane status <target>"));
        const targetArg = args[1];
        const targets = resolveTargets(targetArg, true);
        if (!targets || targets.length === 0) return _out(chalk.red(`  Invalid target: ${targetArg}`));
        
        const names = loadTargetNames();
        if (_rt.getHumaneMode) {
            for (const chat_id of targets) {
                const mode = _rt.getHumaneMode(chat_id);
                _out('');
                _div(`HUMANE MODE: ${names[chat_id]?.name || chat_id}`);
                _out(`  Mode:        ${mode.toUpperCase()}`);
                _div();
            }
            _out('');
        } else {
             _out(chalk.red(`  Failed to get humane status`));
        }
    } else {
        _out(chalk.dim(`  Usage: /humane <off|on|status> <target>`));
    }
}


async function cmdStatus() {
    let bridgeOk = false, ollamaOk = false, personality = 'unknown', model = 'unknown';
    if (_rt.httpGet) {
        try {
            const res = await _rt.httpGet(`${_rt.bridgeUrl}/health`);
            if (res.status === 200 && res.body.ok) {
                bridgeOk = true; ollamaOk = !!res.body.ollama;
                personality = res.body.personality || 'unknown'; model = res.body.model || 'unknown';
            }
        } catch {}
    }
    let contactCount = 0;
    try { contactCount = Object.keys(JSON.parse(fs.readFileSync(CONTACTS_FILE, 'utf8'))).length; } catch {}
    
    _out('');
    _div('STATUS');
    _out(`  WhatsApp     ${!!_rt.getClient() ? chalk.green('🟢 Connected') : chalk.red('🔴 Disconnected')}`);
    _out(`  Ollama       ${ollamaOk ? chalk.green('🟢 ' + model) : chalk.red('🔴 Offline')}`);
    _out(`  Bridge       ${bridgeOk ? chalk.green('🟢 :' + _rt.bridgePort) : chalk.red('🔴 Unreachable')}`);
    _out(`  Personality  🎭 ${personality}`);
    _out(`  Chats        💬 ${_rt.targetChatIds.length}`);
    _out(`  Contacts     👥 ${contactCount}`);
    _out(`  Uptime       ⏱  ${_uptime()}`);
    _out(`  Messages     📨 ${stats.received} rx, ${stats.replied} tx`);
    _div();
    _out('');
}


function resolveTargets(targetArg, allowAll = false) {
    if (!targetArg) return null;
    const str = targetArg.toString().toLowerCase();
    if (str === 'all') {
        return allowAll ? [..._rt.targetChatIds] : null;
    }
    if (_rt.targetChatIds.includes(targetArg)) {
        return [targetArg];
    }
    const idx = parseInt(targetArg, 10);
    if (!isNaN(idx) && idx >= 1 && idx <= _rt.targetChatIds.length) {
        return [_rt.targetChatIds[idx - 1]];
    }
    return null;
}


function cmdTargets(args) {
    const names = loadTargetNames();
    
    if (args.length === 0) {
        _out('');
        _div(`TARGETS (${_rt.targetChatIds.length})`);
        if (_rt.targetChatIds.length === 0) {
            _out(chalk.dim('  (no target chats configured)'));
        }
        for (let i = 0; i < _rt.targetChatIds.length; i++) {
            const id = _rt.targetChatIds[i];
            const name = names[id]?.name || 'Unknown';
            const enabledStr = (names[id]?.enabled === false) ? chalk.red('  [DISABLED]') : chalk.green('  [ENABLED]');
            const type = id.includes('@g.us') ? chalk.blue('GROUP') : id.includes('@c.us') ? chalk.magenta('DM   ') : chalk.yellow('OTHER');
            _out(`  [${i+1}] ${chalk.bold(name)}${enabledStr}`);
            _out(`      ${type}  ${chalk.dim(id)}\n`);
        }
        _div();
        _out('');
        return;
    }
    
    const sub = args[0].toLowerCase();
    
    if (sub === 'add' && args.length >= 3) {
        const id = args[args.length - 1];
        const name = args.slice(1, -1).join(' ');
        if (_rt.targetChatIds.includes(id)) return _out(chalk.yellow(`  Target ${id} is already in the list.`));
        _rt.targetChatIds.push(id);
        _rt.updateTargets(_rt.targetChatIds);
        updateEnvTargets(_rt.targetChatIds);
        names[id] = { name };
        saveTargetNames(names);
        _out(chalk.green(`  ✓ Added [${_rt.targetChatIds.length}] ${name} (${id})`));
    } 
    else if (sub === 'remove' && args.length === 2) {
        const targetArg = args[1];
        const targets = resolveTargets(targetArg, false);
        if (!targets || targets.length === 0) return _out(chalk.red(`  Invalid target: ${targetArg}`));
        const removed = targets[0];
        const idx = _rt.targetChatIds.indexOf(removed);
        _rt.targetChatIds.splice(idx, 1);
        _rt.updateTargets(_rt.targetChatIds);
        updateEnvTargets(_rt.targetChatIds);
        _out(chalk.green(`  ✓ Removed target ${idx+1}: ${names[removed]?.name || removed}`));
    }
    else if ((sub === 'rename' || sub === 'edit') && args.length >= 3) {
        const targetArg = args[args.length - 1];
        const targets = resolveTargets(targetArg, false);
        if (!targets || targets.length === 0) return _out(chalk.red(`  Invalid target: ${targetArg}`));
        const id = targets[0];
        const newName = args.slice(1, -1).join(' ');
        if (!names[id]) names[id] = {};
        names[id].name = newName;
        saveTargetNames(names);
        const idx = _rt.targetChatIds.indexOf(id) + 1;
        _out(chalk.green(`  ✓ Renamed target ${idx} to ${newName}`));
    }
    else if ((sub === 'enable' || sub === 'disable') && args.length === 2) {
        const targetArg = args[1];
        const targets = resolveTargets(targetArg, true);
        if (!targets || targets.length === 0) return _out(chalk.red(`  Invalid target: ${targetArg}`));
        for (const id of targets) {
            if (!names[id]) names[id] = { name: 'Unknown' };
            names[id].enabled = (sub === 'enable');
        }
        saveTargetNames(names);
        _out(chalk.green(`  ✓ ${targets.length} target(s) are now ${sub}d for AI replies.`));
    }
    else {
        _out(`  Usage:`);
        _out(`    /targets`);
        _out(`    /targets add <name> <chat_id>`);
        _out(`    /targets remove <target>`);
        _out(`    /targets rename <new_name> <target>`);
        _out(`    /targets enable <target>`);
        _out(`    /targets disable <target>`);
    }
}


async function cmdSend(args) {
    if (args.length < 2) {
        _out(chalk.dim("  Usage: /send <message> <target>"));
        return;
    }
    const targetArg = args[args.length - 1];
    const msg = args.slice(0, -1).join(' ');
    
    let toSend = [];
    if (targetArg.toLowerCase() === 'all') {
        toSend = [..._rt.targetChatIds];
    } else {
        const indices = targetArg.split(',').map(n => n.trim());
        for (const arg of indices) {
            const targets = resolveTargets(arg, false);
            if (!targets || targets.length === 0) {
                return _out(chalk.red(`  Invalid target: ${arg}`));
            }
            toSend.push(targets[0]);
        }
        toSend = [...new Set(toSend)];
    }
    
    const names = loadTargetNames();
    
    _out('');
    _div('MANUAL SEND');
    _out(`  Targets:${targetArg.toLowerCase() === 'all' ? ' ALL (' + toSend.length + ')' : ''}`);
    if (targetArg.toLowerCase() !== 'all') {
        toSend.forEach(id => {
            const idx = _rt.targetChatIds.indexOf(id) + 1;
            _out(`    [${idx}] ${names[id]?.name || id}`);
        });
    }
    _out(`  Message: ${msg}\n`);
    
    const client = _rt.getClient();
    if (!client) return _out(chalk.red('  WhatsApp client disconnected. Cannot send.'));
    
    for (const id of toSend) {
        try {
            await client.sendMessage(id, msg);
            const idx = _rt.targetChatIds.indexOf(id) + 1;
            const name = names[id]?.name || id;
            _out(`  ${chalk.green('✓')} [${idx}] ${name}`);
        } catch (e) {
            _out(`  ${chalk.red('✗')} Failed to send to ${id}: ${e.message}`);
        }
    }
    _div();
    _out('');
}


async function cmdModel(args) {
    if (args.length === 0 || args[0].toLowerCase() === 'list') {
        let activeModel = 'unknown';
        try {
            if (_rt.httpGet) {
                const health = await _rt.httpGet(`${_rt.bridgeUrl}/health`);
                if (health.status === 200 && health.body.ok) activeModel = health.body.model;
            }
        } catch {}
        
        let installed = [];
        try {
            if (_rt.httpGet) {
                const res = await _rt.httpGet('http://localhost:11434/api/tags');
                if (res.status === 200 && res.body.models) {
                    installed = res.body.models.map(m => m.name);
                }
            }
        } catch {
            installed = ['(Could not fetch from Ollama)'];
        }
        
        _out('');
        _div('MODELS');
        _out(`  Active:`);
        _out(`    ${chalk.bold.green(activeModel)}\n`);
        _out(`  Installed:`);
        for (let i = 0; i < installed.length; i++) {
            _out(`    [${i+1}] ${installed[i]}`);
        }
        _div();
        _out('');
        return;
    }
    
    let modelName = args[0];
    if (modelName.toLowerCase() === 'on') {
        _globalState.model_enabled = true;
        saveGlobalState();
        return _out(chalk.green(`  ✓ Global AI model responses ENABLED`));
    }
    if (modelName.toLowerCase() === 'off') {
        _globalState.model_enabled = false;
        saveGlobalState();
        return _out(chalk.green(`  ✓ Global AI model responses DISABLED`));
    }
    
    if (modelName.toLowerCase() === 'use' || modelName.toLowerCase() === 'switch') {
        if (args.length < 2) return _out(chalk.dim("  Usage: /model use <name>"));
        modelName = args[1];
    }
    
    // Check if installed
    try {
        if (_rt.httpGet) {
            const res = await _rt.httpGet('http://localhost:11434/api/tags');
            if (res.status === 200 && res.body.models) {
                const installed = res.body.models.map(m => m.name);
                if (!installed.includes(modelName)) {
                    return _out(chalk.yellow(`  Model '${modelName}' is not installed in Ollama. Pull it first.`));
                }
            }
        }
    } catch {}
    
    // Switch via bridge
    try {
        if (_rt.httpPost) {
            const res = await _rt.httpPost(`${_rt.bridgeUrl}/model`, { model: modelName });
            if (res.status === 200) {
                _out(chalk.green(`  ✓ Switched active model to: ${modelName}`));
            } else {
                _out(chalk.red(`  Failed to switch model: HTTP ${res.status}`));
            }
        }
    } catch (err) {
        _out(chalk.red(`  Bridge error: ${err.message}`));
    }
}


async function cmdPersonality(args) {
    if (args.length === 0) {
        let active = 'unknown';
        try {
            if (_rt.httpGet) {
                const res = await _rt.httpGet(`${_rt.bridgeUrl}/health`);
                if (res.status === 200) active = res.body.personality;
            }
        } catch {}
        _out('');
        _div('PERSONALITY');
        _out(`  Active: ${chalk.bold.green(active)}`);
        _div();
        _out('');
        return;
    }
    
    const sub = args[0].toLowerCase();
    if (sub === 'list') {
        let personalities = [];
        try {
            if (_rt.httpGet) {
                const res = await _rt.httpGet(`${_rt.bridgeUrl}/personality/list`);
                if (res.status === 200 && res.body.ok) personalities = res.body.list;
            }
        } catch (e) {
            return _out(chalk.red(`  Bridge error: ${e.message}`));
        }
        _out('');
        _div('AVAILABLE PERSONALITIES');
        for (let i = 0; i < personalities.length; i++) {
            _out(`  [${i+1}] ${personalities[i]}`);
        }
        _div();
        _out('');
    }
    else if (sub === 'use' || sub === 'switch') {
        if (args.length < 3) return _out(chalk.dim("  Usage: /personality use <name> <target>"));
        const pName = args[1];
        const targetArg = args[args.length - 1];
        const targets = resolveTargets(targetArg, true);
        if (!targets || targets.length === 0) return _out(chalk.red(`  Invalid target: ${targetArg}`));
        
        let successCount = 0;
        for (const chat_id of targets) {
            try {
                if (_rt.httpPost) {
                    const res = await _rt.httpPost(`${_rt.bridgeUrl}/personality/use`, { chat_id, personality: pName });
                    if (res.status === 200 && res.body.ok) {
                        successCount++;
                    } else {
                        _out(chalk.red(`  Failed for ${chat_id}: ${res.body.error || 'Unknown error'}`));
                    }
                }
            } catch (err) {
                _out(chalk.red(`  Bridge error for ${chat_id}: ${err.message}`));
            }
        }
        if (successCount > 0) {
            _out(chalk.green(`  ✓ Switched personality to '${pName}' for ${successCount} target(s).`));
        }
    }
    else if (sub === 'default') {
        if (args.length < 2) return _out(chalk.dim("  Usage: /personality default <name>"));
        const pName = args[1];
        _globalState.default_personality = pName;
        saveGlobalState();
        _out(chalk.green(`  ✓ Global default personality set to: ${pName}`));
    }
    else if (sub === 'create') {
        _out(chalk.yellow(`  Creation via terminal not implemented yet.`));
    }
    else {
        _out(chalk.dim(`  Usage: /personality [list | use <name> <target> | default <name>]`));
    }
}



async function cmdInternet(args) {
    if (args.length === 0) {
        _out(chalk.dim(`  Usage: /internet <off|auto|on|status> <target>`));
        return;
    }
    const sub = args[0].toLowerCase();
    
    if (sub === 'off' || sub === 'auto' || sub === 'on') {
        if (args.length < 2) return _out(chalk.dim("  Usage: /internet <off|auto|on> <target>"));
        const targetArg = args[1];
        const targets = resolveTargets(targetArg, true);
        if (!targets || targets.length === 0) return _out(chalk.red(`  Invalid target: ${targetArg}`));
        
        const names = loadTargetNames();
        let successCount = 0;
        for (const chat_id of targets) {
            try {
                if (_rt.httpPost) {
                    const res = await _rt.httpPost(`${_rt.bridgeUrl}/internet/set`, { chat_id, mode: sub });
                    if (res.status === 200 && res.body.ok) {
                        successCount++;
                    } else {
                        _out(chalk.red(`  Failed for ${chat_id}: ${res.body.error || 'Unknown error'}`));
                    }
                }
            } catch (err) {
                _out(chalk.red(`  Bridge error for ${chat_id}: ${err.message}`));
            }
        }
        if (successCount > 0) {
            _out(chalk.green(`  ✓ Internet mode set to ${sub.toUpperCase()} for ${successCount} target(s).`));
        }
    }
    else if (sub === 'status') {
        if (args.length < 2) return _out(chalk.dim("  Usage: /internet status <target>"));
        const targetArg = args[1];
        const targets = resolveTargets(targetArg, true);
        if (!targets || targets.length === 0) return _out(chalk.red(`  Invalid target: ${targetArg}`));
        
        const names = loadTargetNames();
        for (const chat_id of targets) {
            try {
                if (_rt.httpPost) {
                    const res = await _rt.httpPost(`${_rt.bridgeUrl}/internet/status`, { chat_id });
                    if (res.status === 200 && res.body.ok) {
                        const b = res.body;
                        _out('');
                        _div(`INTERNET ACCESS: ${names[chat_id]?.name || chat_id}`);
                        _out(`  Mode:        ${b.mode}`);
                        _out(`  Connection:  ${b.connection}`);
                        _out(`  Web tools:   ${b.web_tools}`);
                        _div();
                    } else {
                        _out(chalk.red(`  Failed to get internet status for ${chat_id}`));
                    }
                }
            } catch (err) {
                _out(chalk.red(`  Bridge error for ${chat_id}: ${err.message}`));
            }
        }
        _out('');
    } else {
        _out(chalk.dim(`  Usage: /internet <off|auto|on|status> <target>`));
    }
}


function cmdContacts() {
    let contacts = {};
    try { contacts = JSON.parse(fs.readFileSync(CONTACTS_FILE, 'utf8')); } catch { return _out('No contacts file.'); }
    const entries = Object.entries(contacts);
    _out('');
    _div(`CONTACTS (${entries.length})`);
    for (const [jid, info] of entries) {
        const name = chalk.bold((info.name || '?').padEnd(14));
        const short = jid.length > 24 ? '…' + jid.slice(-23) : jid;
        _out(`  ${name} ${chalk.dim(short)}`);
    }
    _div();
    _out('');
}

function cmdHistory(n) {
    const items = _history.slice(-n);
    _out('');
    _div(`HISTORY (last ${items.length})`);
    if (items.length === 0) _out(chalk.dim('  No activity yet.'));
    for (const e of items) {
        const t = e.ts.toLocaleTimeString('en-GB', { hour12: false });
        if (e.type === 'msg') _out(`  ${chalk.gray(t)}  💬 ${chalk.bold(e.sender)}${e.chat ? chalk.gray(' @ ' + e.chat) : ''}  "${_trunc(e.text, 50)}"`);
        else _out(`  ${chalk.gray(t)}  🤖 ${chalk.green('Preetam')}  "${chalk.green(_trunc(e.text, 50))}"`);
    }
    _div();
    _out('');
}

function cmdStats() {
    _out('');
    _div('STATISTICS');
    _out(`  Messages received   ${stats.received}`);
    _out(`  Replies sent        ${stats.replied}`);
    _out(`  Duplicates skipped  ${stats.duplicates}`);
    _out(`  Messages ignored    ${stats.ignored}`);
    _out(`  Errors              ${stats.errors}`);
    _out(`  Uptime              ${_uptime()}`);
    _div();
    _out('');
}

function cmdMemory() {
    _out('');
    _div('MEMORY');
    try {
        const files = fs.readdirSync(CONVERSATIONS_DIR).filter(f => f.startsWith('session_') && f.endsWith('.json'));
        _out(`  Session files  ${files.length}`);
        if (files.length > 0) {
            const latest = files.sort().pop();
            _out(`  Latest         ${latest.replace('session_', '').replace('.json', '')}`);
        }
    } catch { _out(chalk.dim('  Could not read conversations.')); }
    _div();
    _out('');
}

async function cmdPing() {
    _out('');
    _div('PING');
    _out(`  WhatsApp  ${!!_rt.getClient() ? chalk.green('✓ Connected') : chalk.red('✗ Disconnected')}`);
    if (_rt.httpGet) {
        try {
            const t0 = Date.now();
            const res = await _rt.httpGet(`${_rt.bridgeUrl}/health`);
            const ms = Date.now() - t0;
            if (res.status === 200 && res.body.ok) {
                _out(`  Bridge   ${chalk.green('✓ Online')} ${chalk.dim(`(${ms}ms)`)}`);
                _out(`  Ollama   ${res.body.ollama ? chalk.green('✓ Online') : chalk.red('✗ Offline')}`);
            } else _out(`  Bridge   ${chalk.red('✗ Error')}`);
        } catch (e) { _out(`  Bridge   ${chalk.red('✗ Unreachable')} — ${e.message}`); }
    }
    _div();
    _out('');
}


function cmdHelp() {
    _out('');
    _div('COMMANDS');
    const cmds = [
        ['/targets',      'List targets'],
        ['/targets ...',  '/targets <add|remove|rename|enable|disable> <target>'],
        ['/send',         '/send <message> <target> (target: all, 1, or chat_id)'],
        ['/model',        'Manage global AI responses (on, off) or models (list, use)'],
        ['/personality',  '/personality <list|use|default> <target>'],
        ['/internet',     '/internet <off|auto|on|status> <target>'],
        ['/humane',       '/humane <off|on|status> <target>'],
        ['/status',       'Connection & runtime status dashboard'],
        ['/contacts',     'Show known contacts registry'],
        ['/history [n]',  'Show last n message events'],
        ['/memory',       'Conversation memory info'],
        ['/quiet',        'Reduce output to messages + errors only'],
        ['/clear',        'Clear terminal screen'],
        ['/about',        'Project info'],
        ['/exit',         'Graceful shutdown'],
    ];
    if (_globalState.devmode) {
        cmds.push(['/devmode', 'Toggle Developer Mode (enable, disable)']);
        cmds.push(['/ping', 'Health check all services (DEV)']);
        cmds.push(['/stats', 'Runtime statistics (DEV)']);
        cmds.push(['/verbose', 'Toggle verbose logging on/off (DEV)']);
    }
    for (const [cmd, desc] of cmds) _out(`  ${chalk.cyan(cmd.padEnd(16))} ${chalk.dim(desc)}`);
    _div();
    _out('');
}


// ═══════════════════════════════════════════════════════════════════
//  COMMAND DISPATCH
// ═══════════════════════════════════════════════════════════════════

async function _dispatch(input) {
    const trimmed = input.trim();
    if (!trimmed) return;
    if (!trimmed.startsWith('/')) return _out(chalk.dim(`  Unknown input. Type ${chalk.cyan('/help')} for commands.`));

    const parts = trimmed.split(/\s+/);
    const cmd   = parts[0].toLowerCase();
    const args  = parts.slice(1);

    const requireDev = () => {
        if (!_globalState.devmode) {
            _out(chalk.red(`  Command ${cmd} requires Developer Mode. Use /devmode enable first.`));
            return false;
        }
        return true;
    };

    switch (cmd) {
        case '/help':  case '/h':       cmdHelp(); break;
        case '/status':                 await cmdStatus(); break;
        case '/targets': case '/chats': cmdTargets(args); break;
        case '/send':                   await cmdSend(args); break;
        case '/model':                  await cmdModel(args); break;
        case '/personality':            await cmdPersonality(args); break;
        case '/internet':               await cmdInternet(args); break;
        case '/humane':                 await cmdHumane(args); break;
        case '/contacts':               cmdContacts(); break;
        case '/history':                cmdHistory(parseInt(args[0]) || 20); break;
        case '/memory':                 cmdMemory(); break;
        case '/ping':                   if (requireDev()) await cmdPing(); break;
        case '/stats':                  if (requireDev()) cmdStats(); break;
        case '/verbose':
            if (requireDev()) {
                if (args[0] === 'on') { _verbose = true; _quiet = false; _out(chalk.cyan('  Verbose mode enabled')); }
                else if (args[0] === 'off') { _verbose = false; _out(chalk.cyan('  Verbose mode disabled')); }
                else _out(chalk.dim('  Usage: /verbose <on|off>'));
            }
            break;
        case '/devmode':
            if (args[0] === 'enable') { _globalState.devmode = true; saveGlobalState(); _out(chalk.cyan('  Developer Mode enabled')); }
            else if (args[0] === 'disable') { _globalState.devmode = false; saveGlobalState(); _out(chalk.cyan('  Developer Mode disabled')); }
            else _out(chalk.dim(`  Developer Mode is currently ${_globalState.devmode ? 'ENABLED' : 'DISABLED'}. Usage: /devmode <enable|disable>`));
            break;
        case '/quiet':                  _quiet = true; _out(chalk.cyan('  Quiet mode enabled')); break;
        case '/clear':                  console.clear(); if (rl) rl.prompt(true); break;
        case '/about':                  _out(`\n  ${chalk.bold('Mini Jarvis — WhatsApp Bot')}\n  Architecture   WhatsApp → Node.js → Python Flask → Ollama\n`); break;
        case '/exit':   case '/quit':   _out(chalk.cyan('  Shutting down…')); if (_rt.shutdown) _rt.shutdown('CLI /exit'); else process.exit(0); break;
        default:                        _out(chalk.dim(`  Unknown command: ${cmd}. Type ${chalk.cyan('/help')} for commands.`));
    }
}

// ═══════════════════════════════════════════════════════════════════
//  READLINE MANAGEMENT
// ═══════════════════════════════════════════════════════════════════

function startPrompt() {
    if (rl) return;
    rl = rl_mod.createInterface({
        input:  process.stdin,
        output: process.stdout,
        prompt: chalk.cyan('jarvis') + chalk.dim('> '),
        terminal: true,
    });
    rl.on('line', async (line) => {
        await _dispatch(line);
        if (rl) rl.prompt();
    });
    rl.on('close', () => { if (_rt.shutdown) _rt.shutdown('SIGINT'); });
    _promptActive = true;
    _out(`\n${chalk.dim(`  Type ${chalk.cyan('/help')} for commands`)}\n`);
    rl.prompt();
}

function pausePrompt() {
    if (!rl) return;
    _promptActive = false;
    rl_mod.clearLine(process.stdout, 0);
    rl_mod.cursorTo(process.stdout, 0);
}

function resumePrompt() {
    if (!rl) return;
    _promptActive = true;
    rl.prompt(true);
}

function close() {
    _promptActive = false;
    if (rl) {
        const r = rl;
        rl = null;
        r.removeAllListeners('close');
        r.close();
    }
}

function init(config) { Object.assign(_rt, config); }

function getDefaultPersonality() {
    return _globalState.default_personality;
}

module.exports = {
    init, banner, startPrompt, pausePrompt, resumePrompt, close,
    system, success, message, reply, skip, skipDup, warn, error, verbose, bridgePoll, stats,
    isModelEnabled, isTargetEnabled, getDefaultPersonality
};
