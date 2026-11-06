#!/usr/bin/env node

const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const rootDir = path.resolve(__dirname, '..');
const localBat = path.join(rootDir, 'cmdai.bat');

if (fs.existsSync(localBat)) {
    // Running from local repository or cloned folder
    const child = spawn('cmd.exe', ['/c', localBat, ...process.argv.slice(2)], {
        stdio: 'inherit',
        cwd: process.cwd()
    });
    child.on('exit', (code) => process.exit(code || 0));
} else {
    // Running via npx / remote npm
    const psCmd = 'irm https://raw.githubusercontent.com/Krzyzyk33/CMDAI-CODE/main/install.ps1 | iex';
    const child = spawn('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', psCmd], {
        stdio: 'inherit',
        cwd: process.cwd()
    });
    child.on('exit', (code) => process.exit(code || 0));
}
