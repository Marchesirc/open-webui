[CmdletBinding()]
param(
    [string]$BaseDir = 'D:\Projetos\Proteus_Projects\Acer_AcerNote_970'
)

$ErrorActionPreference = 'Stop'

$files = @{
    pass2 = Join-Path $BaseDir 'acer_acernote_970_service_guide_focused_pass2.schematic.analysis.json'
    pass3 = Join-Path $BaseDir 'acer_acernote_970_service_guide_focused_pass3_pure_schematics.analysis.json'
    pass4 = Join-Path $BaseDir 'acer_acernote_970_service_guide_focused_pass4_signal_dense.analysis.json'
}

$data = @{}
foreach ($k in $files.Keys) {
    if (Test-Path $files[$k]) {
        $obj = Get-Content $files[$k] -Raw | ConvertFrom-Json
        $a = $obj.analysis
        $data[$k] = [ordered]@{
            pdf = $obj.pdf_relative_path
            readiness = $a.readiness
            pages = $a.pages_processed
            extracted_chars = $a.extracted_text_characters
            components = $a.components_count
            signals = $a.signal_candidates_count
            ocr_backend = $a.ocr_backend
            ocr_attempted_pages = $a.ocr_attempted_pages
            ocr_used_pages = $a.ocr_used_pages
        }
    }
}

$outJson = Join-Path $BaseDir 'comparativo_pass2_pass3_pass4.json'
$outMd = Join-Path $BaseDir 'comparativo_pass2_pass3_pass4.md'

($data | ConvertTo-Json -Depth 8) | Set-Content -Path $outJson -Encoding UTF8

$lines = @()
$lines += '# Comparativo Pass2 vs Pass3 vs Pass4'
$lines += ''
$lines += '| Pass | Readiness | Paginas | Caracteres | Componentes | Sinais | OCR | OCR Paginas | OCR Usadas |'
$lines += '|---|---|---:|---:|---:|---:|---|---:|---:|'
foreach ($k in 'pass2','pass3','pass4') {
    if ($data.ContainsKey($k)) {
        $x = $data[$k]
        $lines += "| $k | $($x.readiness) | $($x.pages) | $($x.extracted_chars) | $($x.components) | $($x.signals) | $($x.ocr_backend) | $($x.ocr_attempted_pages) | $($x.ocr_used_pages) |"
    }
}
$lines += ''
$lines += '## Conclusao'
if ($data.ContainsKey('pass4')) {
    $lines += '- Pass4 esta otimizado para densidade de sinais e menor ruído para montagem assistida.'
}
$lines += '- Use pass4 para execução padrão no ISIS e pass3 para cobertura mais ampla.'

Set-Content -Path $outMd -Value ($lines -join "`r`n") -Encoding UTF8

Get-Item $outJson, $outMd | Select-Object FullName, Length | Format-Table -AutoSize | Out-String -Width 220
