Add-Type -AssemblyName System.Drawing

$out = 'C:\Users\Atreum\Desktop\MySoft\MSB\extensions\msb-profile-badge\icons'
if (!(Test-Path $out)) {
    New-Item -ItemType Directory -Path $out | Out-Null
}

foreach ($size in 16, 48, 128) {
    $bmp = New-Object System.Drawing.Bitmap $size, $size
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $g.Clear([System.Drawing.Color]::FromArgb(47, 111, 237))

    $fontSize = [Math]::Max([float]($size * 0.55), 6.0)
    $font = New-Object System.Drawing.Font('Segoe UI', $fontSize, [System.Drawing.FontStyle]::Bold)
    $brush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
    $sf = New-Object System.Drawing.StringFormat
    $sf.Alignment = [System.Drawing.StringAlignment]::Center
    $sf.LineAlignment = [System.Drawing.StringAlignment]::Center

    # Use overloaded DrawString with x,y,width,height
    $g.DrawString('M', $font, $brush, 0.0, 0.0, [float]$size, [float]$size, $sf)

    $g.Dispose()
    $font.Dispose()
    $brush.Dispose()

    $path = Join-Path $out ("icon" + $size + ".png")
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    Write-Host ("wrote " + $path)
}

Get-ChildItem $out | Select-Object Name, Length | Format-Table -AutoSize
