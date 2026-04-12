[CmdletBinding()]
param(
    [string]$LanguageCode = 'por',
    [string]$SourceRepo = 'tessdata_best'
)

$ErrorActionPreference = 'Stop'

$tessdataDir = Join-Path $env:LOCALAPPDATA 'Tesseract-OCR\tessdata'
New-Item -ItemType Directory -Path $tessdataDir -Force | Out-Null

$fileName = "$LanguageCode.traineddata"
$downloadUrl = "https://github.com/tesseract-ocr/$SourceRepo/raw/main/$fileName"
$destPath = Join-Path $tessdataDir $fileName

Write-Host "Baixando $fileName em $tessdataDir" -ForegroundColor Cyan
Invoke-WebRequest -Uri $downloadUrl -OutFile $destPath

$systemTessdata = 'C:\Program Files\Tesseract-OCR\tessdata'
foreach ($baseLang in @('eng.traineddata', 'osd.traineddata')) {
    $systemLang = Join-Path $systemTessdata $baseLang
    $userLang = Join-Path $tessdataDir $baseLang
    if ((Test-Path $systemLang) -and -not (Test-Path $userLang)) {
        Copy-Item -Path $systemLang -Destination $userLang -Force
    }
}

$prefix = Split-Path $tessdataDir -Parent
[Environment]::SetEnvironmentVariable('TESSDATA_PREFIX', $prefix, 'User')
$env:TESSDATA_PREFIX = $prefix

Write-Host "Idioma OCR instalado em perfil de usuario." -ForegroundColor Green
Write-Host "Idiomas no perfil de usuario:" -ForegroundColor Cyan
Get-ChildItem $tessdataDir -Filter '*.traineddata' | Select-Object -ExpandProperty Name
Write-Host "TESSDATA_PREFIX (usuario): $prefix" -ForegroundColor Green
Write-Host "Abra uma nova sessao do terminal para herdar a variavel de ambiente." -ForegroundColor Yellow
