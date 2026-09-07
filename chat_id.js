const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');

const client = new Client({
    authStrategy: new LocalAuth()
});

client.on('qr', (qr) => {
    qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
    console.log('✅ Ready');
});

client.on('message', async (message) => {

    console.log('\n═══════════════════════');
    console.log('Message:', message.body);
    console.log('FROM:', message.from);

    if (message.author) {
        console.log('AUTHOR:', message.author);
    }

    console.log('═══════════════════════\n');

});

client.initialize();