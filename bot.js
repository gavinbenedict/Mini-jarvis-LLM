/**
 * bot.js — Mini-Jarvis WhatsApp Bot
 *
 * Architecture:
 *   WhatsApp (whatsapp-web.js)  →  this file  →  jarvis_bridge.py (HTTP)  →  Ollama
 *
 * Key behaviours:
 *   - Waits for the Python bridge to be healthy before starting WhatsApp
 *   - Only replies to chats listed in TARGET_CHAT_IDS (from .env)
 *   - Handles both @c.us DMs and @lid / @g.us group chats
 *   - Fully async — no blocking calls, event loop stays alive
 *   - No fake typing delays — replies instantly
 *   - Reconnects automatically on disconnect
 *   - Graceful shutdown on SIGINT / SIGTERM
 *   - Never silently fails — every error is logged
 *   - Polished CLI dashboard via cli.js
 */

'use strict';

require('dotenv').config();

const http = require('http');
const fs   = require('fs');
const path = require('path');
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const cli = require('./cli');

// ── Config ────────────────────────────────────────────────────────────
// Parse comma-separated list of target chat IDs.
// Falls back to old TARGET_CHAT_ID for backward compatibility.
const TARGET_CHAT_IDS = (process.env.TARGET_CHAT_IDS || process.env.TARGET_CHAT_ID || '')
    .split(',')
    .map(id => id.trim())
    .filter(id => id.length > 0);

const BRIDGE_PORT    = parseInt(process.env.BRIDGE_PORT || '5001', 10);
const BRIDGE_HOST    = '127.0.0.1';
const BRIDGE_URL     = `http://${BRIDGE_HOST}:${BRIDGE_PORT}`;

// How long to wait for the bridge to come up (ms)
const BRIDGE_WAIT_TIMEOUT_MS = 60_000;
const BRIDGE_POLL_INTERVAL_MS = 1_500;

if (TARGET_CHAT_IDS.length === 0) {
    console.error('❌  TARGET_CHAT_IDS is not set in .env — bot will not reply to anything.');
    console.error('    Run `node chat_id.js`, send a message, and copy the FROM value.');
    process.exit(1);
}

// ── ID normalisation ─────────────────────────────────────────────────
/**
 * Normalise a WhatsApp JID so we can compare them reliably.
 * Strips the server suffix (@c.us, @lid, @g.us, @s.whatsapp.net)
 * and returns just the numeric part + its original suffix lowercased.
 *
 * We keep the suffix because a phone number @c.us and a group @g.us
 * can share the same numeric prefix.
 */
function normaliseId(id) {
    if (!id) return '';
    return id.toLowerCase().trim();
}

/**
 * Return true if `from` matches ANY of the configured target chats,
 * regardless of whether the stored ID uses @c.us, @lid, @g.us, or
 * @s.whatsapp.net.
 *
 * Strategy (per target):
 *   1. Exact match (after normalisation)
 *   2. Numeric prefix match — strip the @xxx suffix from both sides
 */
function isTargetChat(from) {
    const normFrom = normaliseId(from);
    const numFrom  = normFrom.split('@')[0];

    return TARGET_CHAT_IDS.some(targetId => {
        const normTarget = normaliseId(targetId);
        if (normFrom === normTarget) return true;

        const numTarget = normTarget.split('@')[0];
        return numFrom === numTarget && numFrom.length > 0;
    });
}

// ── HTTP helper ──────────────────────────────────────────────────────
/**
 * Simple promisified HTTP GET.
 */
function httpGet(url) {
    return new Promise((resolve, reject) => {
        http.get(url, (res) => {
            let data = '';
            res.on('data', (chunk) => { data += chunk; });
            res.on('end', () => {
                try { resolve({ status: res.statusCode, body: JSON.parse(data) }); }
                catch { resolve({ status: res.statusCode, body: data }); }
            });
        }).on('error', reject);
    });
}

/**
 * Simple promisified HTTP POST with JSON body.
 */
function httpPost(url, payload) {
    return new Promise((resolve, reject) => {
        const body = JSON.stringify(payload);
        const options = {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Content-Length': Buffer.byteLength(body),
            },
        };

        const req = http.request(url, options, (res) => {
            let data = '';
            res.on('data', (chunk) => { data += chunk; });
            res.on('end', () => {
                try { resolve({ status: res.statusCode, body: JSON.parse(data) }); }
                catch { resolve({ status: res.statusCode, body: data }); }
            });
        });

        req.on('error', reject);
        req.write(body);
        req.end();
    });
}

// ── Bridge health polling ─────────────────────────────────────────────
/**
 * Poll GET /health until the bridge responds or we time out.
 * Resolves true on success, throws on timeout.
 */
async function waitForBridge() {
    const deadline = Date.now() + BRIDGE_WAIT_TIMEOUT_MS;
    let attempt = 0;

    cli.system(`Waiting for Python bridge on ${BRIDGE_URL} ...`);

    while (Date.now() < deadline) {
        attempt++;
        try {
            const res = await httpGet(`${BRIDGE_URL}/health`);
            if (res.status === 200 && res.body.ok) {
                cli.success(`Bridge ready (attempt ${attempt})`);
                if (!res.body.ollama) {
                    cli.warn('Ollama is not responding — replies may fail.');
                }
                return;
            }
        } catch (_) {
            // Not up yet — keep polling
        }

        cli.bridgePoll(attempt);
        await new Promise((r) => setTimeout(r, BRIDGE_POLL_INTERVAL_MS));
    }

    throw new Error(
        `Bridge did not become ready within ${BRIDGE_WAIT_TIMEOUT_MS / 1000}s. ` +
        `Make sure "python jarvis_bridge.py" is running.`
    );
}

// ── WhatsApp client factory ───────────────────────────────────────────
function createClient() {
    return new Client({
        authStrategy: new LocalAuth(),
        puppeteer: {
            headless: true,
            args: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
            ],
        },
        // Increase internal timeouts for slow machines
        qrMaxRetries: 5,
        restartOnAuthFail: true,
    });
}


// ── Per-chat queue ────────────────────────────────────────────────────
// Ensure that LLM generations for the SAME chat run sequentially to prevent
// context corruption, while DIFFERENT chats run concurrently.
const chatQueues = new Map();

function enqueueChatTask(chatId, taskFn) {
    if (isShuttingDown) return Promise.resolve();
    if (!chatQueues.has(chatId)) {
        chatQueues.set(chatId, Promise.resolve());
    }
    const nextPromise = chatQueues.get(chatId).then(taskFn).catch(err => {
        if (!isShuttingDown) cli.error(`Chat ${chatId} queue error: ${err.message}`);
    });
    chatQueues.set(chatId, nextPromise);
    return nextPromise;
}

// ── Message processing ────────────────────────────────────────────────
// Track message IDs we've already replied to (prevent double-sends on reconnect)
const _repliedIds = new Set();

/**
 * Handle an incoming WhatsApp message.
 * Returns the reply string, or null if we chose not to reply.
 */
async function handleMessage(message) {
    // Ignore own messages
    if (message.fromMe) return null;

    const msgId  = message.id?.id || '';
    const from   = message.from   || '';
    const author = message.author || '';  // populated for group messages
    const body   = (message.body  || '').trim();
    const senderName = message._data?.notifyName || '';  // sender's WhatsApp display name

    // ── Chat filter ──────────────────────────────────────────────
    // For groups:  message.from = group JID,  message.author = sender JID
    // For DMs:     message.from = sender JID, message.author = ''
    //
    // We want to reply only when the CHAT (from) is in TARGET_CHAT_IDS.
    if (!isTargetChat(from)) {
        cli.skip(`${from} — not a target chat`);
        return null;
    }

    // Duplicate guard (in-process)
    if (msgId && _repliedIds.has(msgId)) {
        cli.skipDup(`duplicate message id: ${msgId}`);
        return null;
    }

    // Ignore empty messages (stickers, voice notes, etc.)
    if (!body) {
        cli.skip(`empty/media message from ${from}`);
        return null;
    }

    // Log the incoming message
    const displayLabel = senderName || author || from;
    cli.message(displayLabel, from, body);

    // ── Call bridge ──────────────────────────────────────────────
    let reply = null;
    try {
        const res = await httpPost(`${BRIDGE_URL}/chat`, {
            text:    body,
            sender:  author || from,   // use author for group attribution
            sender_name: senderName,   // sender's WhatsApp display name
            chat_id: from,
        });

        if (res.status === 200 && res.body.ok) {
            reply = (res.body.reply || '').trim();
        } else {
            const errMsg = res.body.error || `HTTP ${res.status}`;
            // Duplicate-skip is not a real error
            if (errMsg === 'duplicate message skipped') {
                cli.skipDup(`duplicate skipped by bridge`);
                return null;
            }
            cli.error(`Bridge error: ${errMsg}`);
        }
    } catch (err) {
        cli.error(`Bridge unreachable: ${err.message}`);
    }

    if (!reply) {
        cli.warn('Got empty reply from bridge — not sending anything');
        return null;
    }

    // Mark as replied
    if (msgId) _repliedIds.add(msgId);
    // Keep set bounded
    if (_repliedIds.size > 500) {
        const first = _repliedIds.values().next().value;
        _repliedIds.delete(first);
    }

    cli.reply(reply);
    return reply;
}

// ── Bot lifecycle ─────────────────────────────────────────────────────
let client = null;
let isShuttingDown = false;
let botReadyTimestamp = null;

function attachListeners(c) {
    c.on('qr', (qr) => {
        console.log('\n📱  Scan the QR code to log in:\n');
        qrcode.generate(qr, { small: true });
        console.log('\n(Waiting for scan...)');
    });

    c.on('authenticated', () => {
        cli.success('Authenticated with WhatsApp');
    });

    c.on('auth_failure', (msg) => {
        cli.error(`Auth failure: ${msg}`);
        cli.error('Delete the .wwebjs_auth folder and restart to re-scan QR.');
    });

    c.on('ready', () => {
        botReadyTimestamp = Math.floor(Date.now() / 1000);
        cli.success('WhatsApp Bot Ready');
        cli.startPrompt();
    });

    // 'message_create' fires for ALL messages (including own).
    // We guard fromMe to process only incoming.
    // We use this single handler to avoid duplicate processing of incoming messages.
    c.on('message_create', (message) => {
        if (isShuttingDown) return;
        if (message.fromMe) return; // skip own sent messages
        if (!botReadyTimestamp) return; // skip pre-ready messages
        if (message.timestamp < botReadyTimestamp) {
            cli.skip(`Ignored historical message (${message.timestamp})`);
            return;
        }
        
        const chatId = message.from || '';
        enqueueChatTask(chatId, async () => {
            if (isShuttingDown) return;
            try {
                const reply = await handleMessage(message);
                if (reply && !isShuttingDown) {
                    await message.reply(reply);
                }
            } catch (err) {
                if (!isShuttingDown) cli.error(`Message_create handler error: ${err.message}`, err.stack);
            }
        });
    });

    c.on('disconnected', (reason) => {
        cli.warn(`Disconnected: ${reason}`);
        if (!isShuttingDown) {
            cli.system('Reconnecting in 5 seconds...');
            setTimeout(() => {
                cli.system('Initialising new client...');
                client = createClient();
                attachListeners(client);
                client.initialize().catch((err) => {
                    cli.error(`Reinit error: ${err.message}`);
                });
            }, 5_000);
        }
    });

    c.on('change_state', (state) => {
        cli.verbose(`WhatsApp state changed: ${state}`);
    });
}

// ── Graceful shutdown ─────────────────────────────────────────────────
function shutdown(signal) {
    if (isShuttingDown) return;       // idempotent — prevent double shutdown
    isShuttingDown = true;

    // Log via cli while it's still open, then close readline
    cli.system(`Received ${signal} — shutting down gracefully...`);
    cli.close(); // close readline prompt (safe even if already closed)

    if (client) {
        // Give client.destroy() up to 10s, then force-exit
        const forceTimer = setTimeout(() => {
            console.log('⏰  Shutdown timed out — forcing exit.');
            process.exit(1);
        }, 10_000);
        forceTimer.unref(); // don't keep the process alive just for this timer

        client.destroy()
            .catch(() => {})
            .finally(() => {
                clearTimeout(forceTimer);
                console.log('👋  Bye!');
                process.exit(0);
            });
    } else {
        console.log('👋  Bye!');
        process.exit(0);
    }
}

process.on('SIGINT',  () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('uncaughtException', (err) => {
    // During shutdown, log safely to stderr without using cli (readline may be closed)
    if (isShuttingDown) {
        console.error(`[UNCAUGHT EXCEPTION during shutdown] ${err.message}`);
        return;
    }
    cli.error(`Uncaught exception: ${err.message}`, err.stack);
    // Don't exit — let the bot keep running unless it's fatal
});
process.on('unhandledRejection', (reason) => {
    if (isShuttingDown) {
        console.error(`[UNHANDLED REJECTION during shutdown] ${reason}`);
        return;
    }
    cli.error(`Unhandled rejection: ${reason}`);
});

// ── Main ──────────────────────────────────────────────────────────────

/**
 * Clean up a stale Chrome/Puppeteer browser that was orphaned by a previous
 * crashed shutdown.  Checks the SingletonLock file to see if the PID is still
 * alive — if it is, kills it.  Does NOT delete .wwebjs_auth or the session.
 */
function cleanStaleBrowser() {
    const lockPath = path.join(__dirname, '.wwebjs_auth', 'session', 'SingletonLock');
    let target;
    try {
        target = fs.readlinkSync(lockPath);           // e.g. "hostname-36316"
    } catch {
        return; // no lock file — nothing stale
    }

    const match = target.match(/-(\d+)$/);
    if (!match) return;

    const pid = parseInt(match[1], 10);
    try {
        process.kill(pid, 0);  // signal 0 = existence check only
    } catch {
        // Process is dead — remove the stale lock so Chrome can start fresh
        try { fs.unlinkSync(lockPath); } catch { /* ignore */ }
        cli.system(`Cleaned stale browser lock (PID ${pid} was dead)`);
        return;
    }

    // Process is alive — kill it so we can take over the session
    cli.warn(`Killing orphaned Chrome browser (PID ${pid})...`);
    try {
        process.kill(pid, 'SIGTERM');
        // Give it a moment to release the lock
        const start = Date.now();
        while (Date.now() - start < 3000) {
            try { process.kill(pid, 0); } catch { break; }
            // busy-wait in small increments (this runs once at startup only)
            const wait = Date.now() + 200;
            while (Date.now() < wait) { /* spin */ }
        }
        // Force kill if still alive
        try { process.kill(pid, 0); process.kill(pid, 'SIGKILL'); } catch { /* already dead */ }
    } catch { /* ignore */ }

    // Remove lock file if still present
    try { fs.unlinkSync(lockPath); } catch { /* ignore */ }
    cli.success('Orphaned browser cleaned up');
}

(async () => {
    // Initialise CLI module
    cli.init({
        bridgeUrl: BRIDGE_URL,
        bridgePort: BRIDGE_PORT,
        targetChatIds: TARGET_CHAT_IDS,
        updateTargets: (ids) => {
            TARGET_CHAT_IDS.length = 0;
            TARGET_CHAT_IDS.push(...ids);
        },
        getClient: () => client,
        httpGet,
        httpPost,
        shutdown
    });

    cli.banner();

    // Step 0: Clean up any stale browser from a previous crash
    cleanStaleBrowser();

    // Step 1: Wait for the Python bridge to be healthy
    try {
        await waitForBridge();
    } catch (err) {
        console.error(`\n❌  ${err.message}`);
        console.error('    Start the bridge first:  python jarvis_bridge.py');
        process.exit(1);
    }

    // Step 2: Start WhatsApp client
    cli.system('Initialising WhatsApp client...');
    client = createClient();
    attachListeners(client);

    try {
        await client.initialize();
    } catch (err) {
        cli.error(`Init error: ${err.message}`, err.stack);
        // Destroy the client/browser before exiting to prevent orphaned Chrome
        if (client) {
            try { await client.destroy(); } catch { /* ignore */ }
        }
        process.exit(1);
    }
})();