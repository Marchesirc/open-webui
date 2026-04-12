[CmdletBinding()]
param(
    [string]$BridgeBaseUrl = 'http://127.0.0.1:8001'
)

$ErrorActionPreference = 'Stop'

# Perfil fixo de producao para o fluxo Acer.
$PdfPath = 'Acer_AcerNote_970/acer_acernote_970_service_guide.pdf'
$CandidatesDir = 'Acer_AcerNote_970/acer_acernote_970_service_guide_schematic_candidates_pass2'
$ProjectFolder = 'Acer_AcerNote_970_Focused_Assistido_Pass4_SinalDenso'
$OcrLanguages = 'eng+por'

Write-Host '=== Pipeline Pass4 Producao ===' -ForegroundColor Cyan
Write-Host "Bridge: $BridgeBaseUrl" -ForegroundColor Gray
Write-Host "PDF: $PdfPath" -ForegroundColor Gray
Write-Host "Projeto destino: $ProjectFolder" -ForegroundColor Gray

$health = Invoke-RestMethod -Uri "$BridgeBaseUrl/health" -Method Get
if (-not $health.ok) {
    throw 'Bridge nao esta saudavel. Execute restart_openwebui_with_proteus_tool.ps1 e tente novamente.'
}

$candidatesBody = @{
    pdf_path = $PdfPath
    max_pages = 279
    top_k = 20
    export_pages = $true
    export_dpi = 260
    use_ocr_if_needed = $true
    ocr_languages = $OcrLanguages
    ocr_min_text_chars = 120
    output_dir = $CandidatesDir
} | ConvertTo-Json -Depth 6
$candidates = Invoke-RestMethod -Uri "$BridgeBaseUrl/pdf/extract-schematic-candidates" -Method Post -ContentType 'application/json' -Body $candidatesBody

$focusBody = @{
    source_pdf_path = $PdfPath
    profile_name = 'acer_pass4_signal_dense'
    candidate_manifest_path = "$CandidatesDir/candidate_pages.json"
} | ConvertTo-Json -Depth 6
$focus = Invoke-RestMethod -Uri "$BridgeBaseUrl/pdf/build-focused-subset" -Method Post -ContentType 'application/json' -Body $focusBody

$analysisOut = ($focus.output_pdf_path -replace '\\.pdf$', '.analysis.json')
$analysisBody = @{
    pdf_path = $focus.output_pdf_path
    max_pages = 20
    persist_output = $true
    output_relative_path = $analysisOut
    use_ocr_if_needed = $true
    ocr_languages = $OcrLanguages
    ocr_min_text_chars = 80
} | ConvertTo-Json -Depth 6
$analysis = Invoke-RestMethod -Uri "$BridgeBaseUrl/pdf/analyze-schematic" -Method Post -ContentType 'application/json' -Body $analysisBody

$assistBody = @{
    project_folder = $ProjectFolder
    analysis_relative_path = $analysisOut
    overwrite = $true
    copy_source_pdf = $true
} | ConvertTo-Json -Depth 6
$assist = Invoke-RestMethod -Uri "$BridgeBaseUrl/proteus/prepare-assisted-project" -Method Post -ContentType 'application/json' -Body $assistBody

$result = [ordered]@{
    health = $health
    candidates = $candidates
    focused = $focus
    analysis = $analysis
    assisted = $assist
}

Write-Host 'Pipeline de producao concluido com sucesso.' -ForegroundColor Green
$result | ConvertTo-Json -Depth 12
