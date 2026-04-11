import fs from 'node:fs';
import path from 'node:path';

const tsconfigPath = path.resolve('.svelte-kit', 'tsconfig.json');

if (!fs.existsSync(tsconfigPath)) {
	console.log(`[ensure-svelte-kit-tsconfig] Skipped: ${tsconfigPath} not found`);
	process.exit(0);
}

const raw = fs.readFileSync(tsconfigPath, 'utf8');
const config = JSON.parse(raw);

config.compilerOptions ??= {};

const requiredOptions = {
	strict: true,
	forceConsistentCasingInFileNames: true
};

let changed = false;
for (const [key, value] of Object.entries(requiredOptions)) {
	if (config.compilerOptions[key] !== value) {
		config.compilerOptions[key] = value;
		changed = true;
	}
}

if (changed) {
	fs.writeFileSync(tsconfigPath, `${JSON.stringify(config, null, '\t')}\n`);
	console.log(`[ensure-svelte-kit-tsconfig] Updated ${tsconfigPath}`);
} else {
	console.log(`[ensure-svelte-kit-tsconfig] Already configured: ${tsconfigPath}`);
}
