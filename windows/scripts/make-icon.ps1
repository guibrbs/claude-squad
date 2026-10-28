<#
.SYNOPSIS
  Gera o ícone do Claude Duo a partir do ícone do Claude instalado, com a cor girada.

.DESCRIPTION
  Lê os PNGs do pacote MSIX do Claude (assets\Square44x44Logo.targetsize-*.png), gira o
  matiz de cada pixel e grava um .ico com PNGs embutidos (16 a 256 px). A arte original
  nunca é versionada: o ícone é gerado na máquina, a partir da cópia instalada.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Out,
    [ValidateRange(0, 359)] [int] $Hue = 200,
    [string] $AssetsDir
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

if (-not $AssetsDir) {
    $package = Get-AppxPackage -Name Claude | Sort-Object Version -Descending | Select-Object -First 1
    if (-not $package) { throw 'O Claude oficial (MSIX) não está instalado.' }
    $AssetsDir = Join-Path $package.InstallLocation 'assets'
}

if (-not ('DuoIcon' -as [type])) {
    Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;

public static class DuoIcon
{
    // Rotates the hue of every pixel, keeping saturation, lightness and alpha.
    public static Bitmap Rotate(Bitmap source, int size, double degrees)
    {
        Bitmap bitmap = new Bitmap(size, size, PixelFormat.Format32bppArgb);
        using (Graphics g = Graphics.FromImage(bitmap))
        {
            g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
            g.DrawImage(source, 0, 0, size, size);
        }
        Rectangle all = new Rectangle(0, 0, size, size);
        BitmapData data = bitmap.LockBits(all, ImageLockMode.ReadWrite, PixelFormat.Format32bppArgb);
        byte[] pixels = new byte[data.Stride * size];
        Marshal.Copy(data.Scan0, pixels, 0, pixels.Length);
        for (int i = 0; i < pixels.Length; i += 4)
        {
            double b = pixels[i] / 255.0, gr = pixels[i + 1] / 255.0, r = pixels[i + 2] / 255.0;
            double max = Math.Max(r, Math.Max(gr, b)), min = Math.Min(r, Math.Min(gr, b));
            double l = (max + min) / 2, h = 0, s = 0, d = max - min;
            if (d > 1e-9)
            {
                s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
                if (max == r) h = ((gr - b) / d + (gr < b ? 6 : 0)) * 60;
                else if (max == gr) h = ((b - r) / d + 2) * 60;
                else h = ((r - gr) / d + 4) * 60;
            }
            h = (h + degrees) % 360;
            double q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
            pixels[i + 2] = Channel(p, q, h + 120);
            pixels[i + 1] = Channel(p, q, h);
            pixels[i] = Channel(p, q, h - 120);
        }
        Marshal.Copy(pixels, 0, data.Scan0, pixels.Length);
        bitmap.UnlockBits(data);
        return bitmap;
    }

    static byte Channel(double p, double q, double h)
    {
        h = ((h % 360) + 360) % 360;
        double v = h < 60 ? p + (q - p) * h / 60 : h < 180 ? q : h < 240 ? p + (q - p) * (240 - h) / 60 : p;
        return (byte)Math.Round(Math.Max(0, Math.Min(1, v)) * 255);
    }
}
'@
}

$sizes = 16, 24, 32, 48, 64, 256
$largest = Join-Path $AssetsDir 'Square44x44Logo.targetsize-256.png'
if (-not (Test-Path $largest)) { throw "Ícone do Claude não encontrado em $AssetsDir" }

$images = foreach ($size in $sizes) {
    $own = Join-Path $AssetsDir "Square44x44Logo.targetsize-$size.png"
    $source = [System.Drawing.Bitmap]::FromFile($(if (Test-Path $own) { $own } else { $largest }))
    try {
        $rotated = [DuoIcon]::Rotate($source, $size, $Hue)
        $stream = New-Object System.IO.MemoryStream
        $rotated.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
        $rotated.Dispose()
        , $stream.ToArray()
    } finally { $source.Dispose() }
}

# ICO: header, one 16-byte entry per image, then the PNGs.
$file = New-Object System.IO.MemoryStream
$writer = New-Object System.IO.BinaryWriter $file
$writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]$sizes.Count)
$offset = 6 + 16 * $sizes.Count
for ($i = 0; $i -lt $sizes.Count; $i++) {
    $edge = if ($sizes[$i] -ge 256) { 0 } else { $sizes[$i] }
    $writer.Write([byte]$edge); $writer.Write([byte]$edge); $writer.Write([byte]0); $writer.Write([byte]0)
    $writer.Write([uint16]1); $writer.Write([uint16]32)
    $writer.Write([uint32]$images[$i].Length); $writer.Write([uint32]$offset)
    $offset += $images[$i].Length
}
foreach ($image in $images) { $writer.Write([byte[]]$image) }
$writer.Flush()
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Out) | Out-Null
[System.IO.File]::WriteAllBytes($Out, $file.ToArray())
$writer.Dispose()
Write-Verbose "Ícone gravado em $Out"
