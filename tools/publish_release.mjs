import fs from 'node:fs/promises'
import path from 'node:path'

const url = (process.env.YURLAPS_SUPABASE_URL || '').replace(/\/$/, '')
const key = process.env.YURLAPS_SUPABASE_SERVICE_ROLE_KEY
const bucket = process.env.YURLAPS_RELEASE_BUCKET || 'yurlaps-releases'
const dir = process.env.YURLAPS_RELEASE_DIR || 'dist-electron'
const version = process.env.YURLAPS_RELEASE_VERSION || ''
const repo = process.env.GITHUB_REPOSITORY || ''
const tag = process.env.GITHUB_REF_NAME || `v${version}`
const githubToken = process.env.GITHUB_TOKEN || ''
if (!url || !key || !version) throw new Error('Supabase release variables are incomplete')
if (!repo || !githubToken) throw new Error('GitHub release variables are incomplete')

const exeName = `YurLaps-Setup-${version}.exe`
const blockmapName = `${exeName}.blockmap`
const latestName = 'latest.yml'
const read = async name => fs.readFile(path.join(dir, name))
const exe = await read(exeName)
const blockmap = await read(blockmapName).catch(() => null)
let latest = (await read(latestName)).toString('utf8')
const githubAssetUrl = `https://github.com/${repo}/releases/download/${encodeURIComponent(tag)}/${encodeURIComponent(exeName)}`
// electron-updater reads latest.yml from the website, then resolves the asset
// URL. Make that URL absolute because the large installer lives on GitHub.
latest = latest.replace(new RegExp(`(url:\\s*)${exeName.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\$&')}`, 'g'), `$1${githubAssetUrl}`)

async function githubUpload(name, data) {
  const release = await fetch(`https://api.github.com/repos/${repo}/releases/tags/${encodeURIComponent(tag)}`, {
    headers: { Accept: 'application/vnd.github+json', Authorization: `Bearer ${githubToken}`, 'X-GitHub-Api-Version': '2022-11-28' },
  })
  if (!release.ok) throw new Error(`GitHub release ${tag} not found; create the release/tag first`)
  const releaseData = await release.json()
  const uploadUrl = releaseData.upload_url.replace(/\{.*$/, '')
  const response = await fetch(`${uploadUrl}?name=${encodeURIComponent(name)}`, {
    method: 'POST', headers: { Accept: 'application/vnd.github+json', Authorization: `Bearer ${githubToken}`, 'Content-Type': 'application/octet-stream', 'Content-Length': String(data.length), 'X-GitHub-Api-Version': '2022-11-28' }, body: data,
  })
  if (!response.ok && response.status !== 422) throw new Error(`GitHub upload ${name} failed: ${response.status} ${await response.text()}`)
}
await githubUpload(exeName, exe)
if (blockmap) await githubUpload(blockmapName, blockmap)

const base = `${url}/storage/v1/object/${bucket}`
async function supabaseUpload(name, data, contentType) {
  const response = await fetch(`${base}/${encodeURIComponent(name)}`, {
    method: 'POST', headers: { apikey: key, Authorization: `Bearer ${key}`, 'Content-Type': contentType, 'x-upsert': 'true' }, body: data,
  })
  if (!response.ok) throw new Error(`Upload ${name} failed: ${response.status} ${await response.text()}`)
}
await supabaseUpload(latestName, Buffer.from(latest), 'text/yaml')
const manifest = { version, published_at: new Date().toISOString(), notes: process.env.YURLAPS_RELEASE_NOTES || '', installer: { name: exeName, url: githubAssetUrl, size: exe.length } }
await supabaseUpload('manifest.json', Buffer.from(JSON.stringify(manifest, null, 2)), 'application/json')
console.log(`Published YurLaps ${version}: installer on GitHub, manifest/latest.yml on Supabase`)
