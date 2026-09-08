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
 */

'use strict';

require('dotenv').config();

const http = require('http');
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');

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

    console.log(`⏳  Waiting for Python bridge on ${BRIDGE_URL} ...`);

    while (Date.now() < deadline) {
        attempt++;
        try {
            const res = await httpGet(`${BRIDGE_URL}/health`);
            if (res.status === 200 && res.body.ok) {
                console.log(`✅  Bridge ready (attempt ${attempt})`);
                console.log(`    Personality : ${res.body.personality}`);
                console.log(`    Model       : ${res.body.model}`);
                console.log(`    Ollama      : ${res.body.ollama ? 'online' : '⚠️  offline'}`);
                if (!res.body.ollama) {
                    console.warn('⚠️   Ollama is not responding — replies may fail.');
                }
                return;
            }
        } catch (_) {
            // Not up yet — keep polling
        }

        process.stdout.write(`\r    Attempt ${attempt} — bridge not ready yet, retrying...`);
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
        console.log(`[SKIP] ${from} — not a target chat`);
        return null;
    }

    // Duplicate guard (in-process)
    if (msgId && _repliedIds.has(msgId)) {
        console.log(`[SKIP] duplicate message id: ${msgId}`);
        return null;
    }

    // Ignore empty messages (stickers, voice notes, etc.)
    if (!body) {
        console.log(`[SKIP] empty/media message from ${from}`);
        return null;
    }

    // Log the incoming message
    const displayLabel = senderName || author || from;
    console.log(`\n💬 [${displayLabel}] ${body}`);

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
                console.log(`[BRIDGE] duplicate skipped by bridge`);
                return null;
            }
            console.error(`[BRIDGE ERROR] ${errMsg}`);
        }
    } catch (err) {
        console.error(`[BRIDGE UNREACHABLE] ${err.message}`);
    }

    if (!reply) {
        console.warn('[WARN] Got empty reply from bridge — not sending anything');
        return null;
    }

    // Mark as replied
    if (msgId) _repliedIds.add(msgId);
    // Keep set bounded
    if (_repliedIds.size > 500) {
        const first = _repliedIds.values().next().value;
        _repliedIds.delete(first);
    }

    console.log(`🤖 ${reply}`);
    return reply;
}

// ── Bot lifecycle ─────────────────────────────────────────────────────
let client = null;
let isShuttingDown = false;

function attachListeners(c) {
    c.on('qr', (qr) => {
        console.log('\n📱  Scan the QR code to log in:\n');
        qrcode.generate(qr, { small: true });
        console.log('\n(Waiting for scan...)');
    });

    c.on('authenticated', () => {
        console.log('🔐  Authenticated with WhatsApp');
    });

    c.on('auth_failure', (msg) => {
        console.error(`❌  Auth failure: ${msg}`);
        console.error('    Delete the .wwebjs_auth folder and restart to re-scan QR.');
    });

    c.on('ready', () => {
        console.log('\n' + '═'.repeat(50));
        console.log('✅  WhatsApp Bot Ready');
        console.log(`    Target chats: ${TARGET_CHAT_IDS.length} configured`);
        for (const id of TARGET_CHAT_IDS) {
            console.log(`      • ${id}`);
        }
        console.log(`    Bridge      : ${BRIDGE_URL}`);
        console.log(`    Personality : preetam_v1`);
        console.log('═'.repeat(50) + '\n');
    });

    c.on('message', async (message) => {
        try {
            const reply = await handleMessage(message);
            if (reply) {
                await message.reply(reply);
            }
        } catch (err) {
            console.error(`[MESSAGE HANDLER ERROR] ${err.message}`);
            console.error(err.stack);
        }
    });

    // Fallback: 'message_create' fires for ALL messages (including own).
    // In some whatsapp-web.js versions, group messages bypass 'message' due
    // to an erroneous fromMe flag inside the library. We guard fromMe ourselves.
    c.on('message_create', async (message) => {
        if (message.fromMe) return; // skip own sent messages
        try {
            const reply = await handleMessage(message);
            if (reply) {
                await message.reply(reply);
            }
        } catch (err) {
            console.error(`[MESSAGE_CREATE HANDLER ERROR] ${err.message}`);
            console.error(err.stack);
        }
    });

    c.on('disconnected', (reason) => {
        console.warn(`\n⚠️   Disconnected: ${reason}`);
        if (!isShuttingDown) {
            console.log('🔄  Reconnecting in 5 seconds...');
            setTimeout(() => {
                console.log('🔄  Initialising new client...');
                client = createClient();
                attachListeners(client);
                client.initialize().catch((err) => {
                    console.error(`[REINIT ERROR] ${err.message}`);
                });
            }, 5_000);
        }
    });

    c.on('change_state', (state) => {
        console.log(`[STATE] ${state}`);
    });
}

// ── Graceful shutdown ─────────────────────────────────────────────────
function shutdown(signal) {
    console.log(`\n🛑  Received ${signal} — shutting down gracefully...`);
    isShuttingDown = true;
    if (client) {
        client.destroy()
            .catch(() => {})
            .finally(() => {
                console.log('👋  Bye!');
                process.exit(0);
            });
    } else {
        process.exit(0);
    }
}

process.on('SIGINT',  () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('uncaughtException', (err) => {
    console.error(`[UNCAUGHT EXCEPTION] ${err.message}`);
    console.error(err.stack);
    // Don't exit — let the bot keep running unless it's fatal
});
process.on('unhandledRejection', (reason) => {
    console.error(`[UNHANDLED REJECTION] ${reason}`);
});

// ── Main ──────────────────────────────────────────────────────────────
(async () => {
    console.log('\n' + '═'.repeat(50));
    console.log('  Mini-Jarvis WhatsApp Bot  |  Starting...');
    console.log('═'.repeat(50));
    console.log(`  Target chats: ${TARGET_CHAT_IDS.length} configured`);
    for (const id of TARGET_CHAT_IDS) {
        console.log(`    • ${id}`);
    }
    console.log(`  Bridge port : ${BRIDGE_PORT}`);
    console.log('═'.repeat(50) + '\n');

    // Step 1: Wait for the Python bridge to be healthy
    try {
        await waitForBridge();
    } catch (err) {
        console.error(`\n❌  ${err.message}`);
        console.error('    Start the bridge first:  python jarvis_bridge.py');
        process.exit(1);
    }

    // Step 2: Start WhatsApp client
    console.log('\n🔌  Initialising WhatsApp client...\n');
    client = createClient();
    attachListeners(client);

    try {
        await client.initialize();
    } catch (err) {
        console.error(`[INIT ERROR] ${err.message}`);
        console.error(err.stack);
        process.exit(1);
    }
})();