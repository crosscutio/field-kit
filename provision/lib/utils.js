"use strict";
const fs = require("fs");
const path = require("path");
const { pipeline } = require("stream/promises");
const got = require("got");

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

async function downloadToFile(url, outPath) {
  if (fs.existsSync(outPath)) {
    console.log(`    Already exists: ${path.basename(outPath)}`);
    return false;
  }
  await pipeline(got.stream(url), fs.createWriteStream(outPath));
  return true;
}

async function downloadText(url) {
  const { body } = await got(url);
  return body;
}

async function downloadBuffer(url) {
  return got(url).buffer();
}

module.exports = { ensureDir, downloadToFile, downloadText, downloadBuffer };
