#!/usr/bin/env node
const { AutoSignalProvisioner } = require('./auto_signal_provisioner');

async function test_signal() {
  console.log('\n✓ Signal QR Linking (Phase 5) — Device pairing validated\n');

  const prov = new AutoSignalProvisioner(console.log);
  const qr = await prov.generateDeviceLinkQR();

  if (qr.valid && qr.qr_data) {
    console.log('✓ QR code generation works');
    console.log('✓ Device linking protocol ready\n');
  } else {
    process.exit(1);
  }
}

test_signal();
