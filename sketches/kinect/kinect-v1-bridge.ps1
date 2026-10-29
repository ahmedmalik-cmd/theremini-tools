param(
  [int]$Port = 8181,
  [int]$Stride = 6,
  [int]$MinDepthMm = 800,
  [int]$MaxDepthMm = 4000,
  [switch]$NearMode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$assemblyPath = "C:\Program Files\Microsoft SDKs\Kinect\v1.8\Assemblies\Microsoft.Kinect.dll"
$utf8 = [System.Text.Encoding]::UTF8

if (-not (Test-Path -LiteralPath $assemblyPath)) {
  throw "Kinect v1 SDK assembly not found at $assemblyPath"
}

Add-Type -Path $assemblyPath
$depthFormat = [Microsoft.Kinect.DepthImageFormat]::Resolution320x240Fps30

function Clamp([double]$Value, [double]$Min, [double]$Max) {
  return [Math]::Min($Max, [Math]::Max($Min, $Value))
}

function Round3($Value) {
  return [Math]::Round([double]$Value, 3)
}

function New-BridgeSnapshot([bool]$Connected, [string]$ErrorMessage, [object[]]$PointCloud, [object[]]$Players, [hashtable]$Controls) {
  return [ordered]@{
    connected = $Connected
    error = $ErrorMessage
    timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    pointCloud = $PointCloud
    players = $Players
    controls = $Controls
  }
}

function Write-TcpResponse($Stream, [int]$StatusCode, [string]$StatusText, [string]$Body) {
  $headers = @(
    "HTTP/1.1 $StatusCode $StatusText",
    "Content-Type: application/json; charset=utf-8",
    "Access-Control-Allow-Origin: *",
    "Access-Control-Allow-Methods: GET, OPTIONS",
    "Access-Control-Allow-Headers: Content-Type"
  )

  $buffer = $utf8.GetBytes($Body)
  $headers += "Content-Length: $($buffer.Length)"
  $headers += "Connection: close"
  $headers += ""
  $headers += ""
  $headerBytes = $utf8.GetBytes(($headers -join "`r`n"))
  $Stream.Write($headerBytes, 0, $headerBytes.Length)
  $Stream.Write($buffer, 0, $buffer.Length)
  $Stream.Flush()
}

function Write-JsonResponse($Stream, [int]$StatusCode, $Payload) {
  $json = $Payload | ConvertTo-Json -Depth 8 -Compress
  Write-TcpResponse $Stream $StatusCode "OK" $json
}

function Close-EmptyResponse($Stream, [int]$StatusCode = 204) {
  Write-TcpResponse $Stream $StatusCode "No Content" ""
}

function Get-TrackedPlayers($Skeletons) {
  $players = @()

  foreach ($skeleton in $Skeletons) {
    if ($null -eq $skeleton) {
      continue
    }

    if ($skeleton.TrackingState -ne [Microsoft.Kinect.SkeletonTrackingState]::Tracked) {
      continue
    }

    $head = $skeleton.Joints[[Microsoft.Kinect.JointType]::Head]
    $leftHand = $skeleton.Joints[[Microsoft.Kinect.JointType]::HandLeft]
    $rightHand = $skeleton.Joints[[Microsoft.Kinect.JointType]::HandRight]

    $players += [ordered]@{
      trackingId = [int64]$skeleton.TrackingId
      x = Round3 $skeleton.Position.X
      y = Round3 $skeleton.Position.Y
      z = Round3 $skeleton.Position.Z
      head = @(
        Round3 $head.Position.X,
        Round3 $head.Position.Y,
        Round3 $head.Position.Z
      )
      leftHand = @(
        Round3 $leftHand.Position.X,
        Round3 $leftHand.Position.Y,
        Round3 $leftHand.Position.Z
      )
      rightHand = @(
        Round3 $rightHand.Position.X,
        Round3 $rightHand.Position.Y,
        Round3 $rightHand.Position.Z
      )
    }
  }

  return $players | Sort-Object z
}

function Get-ControlSignals($Players) {
  $default = [ordered]@{
    bodyDepth = 0
    personSeparation = 0
    faceFocus = 0
    foilCoupling = 0
    shielding = 0
    ultrasonicDistance = 1
  }

  if ($Players.Count -eq 0) {
    return $default
  }

  $nearest = $Players[0]
  $bodyDepth = Clamp (1 - (($nearest.z - 0.75) / 2.5)) 0 1
  $faceFocus = Clamp (1 - (($nearest.head[2] - 0.65) / 1.8)) 0 1
  $personSeparation = 0

  if ($Players.Count -ge 2) {
    $personSeparation = Clamp ([Math]::Abs($Players[0].x - $Players[1].x) / 1.1) 0 1
  }

  return [ordered]@{
    bodyDepth = [Math]::Round($bodyDepth, 3)
    personSeparation = [Math]::Round($personSeparation, 3)
    faceFocus = [Math]::Round($faceFocus, 3)
    foilCoupling = 0
    shielding = 0
    ultrasonicDistance = 1
  }
}

function Build-PointCloud($Sensor, $DepthPixels, [int]$Width, [int]$Height, [int]$SampleStride, [int]$MinDepth, [int]$MaxDepth) {
  $points = New-Object System.Collections.Generic.List[object]

  for ($y = 0; $y -lt $Height; $y += $SampleStride) {
    for ($x = 0; $x -lt $Width; $x += $SampleStride) {
      $index = $x + ($y * $Width)
      $pixel = $DepthPixels[$index]
      $depth = [int]$pixel.Depth

      if ($depth -lt $MinDepth -or $depth -gt $MaxDepth) {
        continue
      }

      $depthPoint = New-Object Microsoft.Kinect.DepthImagePoint
      $depthPoint.X = $x
      $depthPoint.Y = $y
      $depthPoint.Depth = $depth
      $depthPoint.PlayerIndex = $pixel.PlayerIndex

      $skeletonPoint = $Sensor.CoordinateMapper.MapDepthPointToSkeletonPoint($depthFormat, $depthPoint)
      $points.Add(@(
          Round3 $skeletonPoint.X,
          Round3 $skeletonPoint.Y,
          Round3 $skeletonPoint.Z,
          [int]$pixel.PlayerIndex
        ))
    }
  }

  return $points.ToArray()
}

function Try-StartSensor([Microsoft.Kinect.KinectSensor]$ExistingSensor) {
  if ($ExistingSensor -and $ExistingSensor.Status -eq [Microsoft.Kinect.KinectStatus]::Connected) {
    return $ExistingSensor
  }

  foreach ($candidate in [Microsoft.Kinect.KinectSensor]::KinectSensors) {
    if ($candidate.Status -ne [Microsoft.Kinect.KinectStatus]::Connected) {
      continue
    }

    $candidate.DepthStream.Enable($depthFormat)
    $candidate.SkeletonStream.Enable()

    if ($NearMode.IsPresent) {
      try {
        $candidate.DepthStream.Range = [Microsoft.Kinect.DepthRange]::Near
      } catch {
      }
    }

    $candidate.Start()
    return $candidate
  }

  return $null
}

$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse("127.0.0.1"), $Port)
$listener.Start()

$sensor = $null
$depthPixels = $null
$skeletons = @()
$depthWidth = 0
$depthHeight = 0
$lastReconnectAttempt = [DateTime]::MinValue
$latestSnapshot = New-BridgeSnapshot $false "Bridge started. Waiting for Kinect v1 sensor." @() @() ([ordered]@{
    bodyDepth = 0
    personSeparation = 0
    faceFocus = 0
    foilCoupling = 0
    shielding = 0
    ultrasonicDistance = 1
  })

Write-Host "Kinect v1 bridge listening on http://127.0.0.1:$Port/"

try {
  while ($true) {
    if ($listener.Pending()) {
      $client = $listener.AcceptTcpClient()

      try {
        $stream = $client.GetStream()
        $reader = [System.IO.StreamReader]::new($stream, $utf8, $false, 1024, $true)
        $requestLine = $reader.ReadLine()

        if (-not $requestLine) {
          Close-EmptyResponse $stream 400
          continue
        }

        while (($line = $reader.ReadLine()) -ne "") {
          if ($null -eq $line) {
            break
          }
        }

        $parts = $requestLine.Split(" ")
        $method = if ($parts.Length -ge 1) { $parts[0] } else { "GET" }
        $path = if ($parts.Length -ge 2) { ($parts[1] -split "\?")[0] } else { "/" }

        if ($method -eq "OPTIONS") {
          Close-EmptyResponse $stream 204
          continue
        }

        switch ($path) {
          "/snapshot" {
            Write-JsonResponse $stream 200 $latestSnapshot
          }
          "/health" {
            Write-JsonResponse $stream 200 ([ordered]@{
                ok = $true
                connected = $latestSnapshot.connected
                timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
              })
          }
          default {
            Write-JsonResponse $stream 200 ([ordered]@{
                service = "kinect-v1-bridge"
                snapshot = "/snapshot"
                health = "/health"
              })
          }
        }
      } finally {
        $client.Close()
      }
    }

    if (($null -eq $sensor -or $sensor.Status -ne [Microsoft.Kinect.KinectStatus]::Connected) -and ((Get-Date) - $lastReconnectAttempt).TotalSeconds -ge 1) {
      $lastReconnectAttempt = Get-Date

      if ($sensor) {
        try {
          $sensor.Stop()
        } catch {
        }
        $sensor = $null
      }

      try {
        $sensor = Try-StartSensor $sensor
      } catch {
        $sensor = $null
        $latestSnapshot = New-BridgeSnapshot $false $_.Exception.Message @() @() $latestSnapshot.controls
      }

      if ($sensor) {
        $depthWidth = $sensor.DepthStream.FrameWidth
        $depthHeight = $sensor.DepthStream.FrameHeight
        $depthPixels = New-Object "Microsoft.Kinect.DepthImagePixel[]" $sensor.DepthStream.FramePixelDataLength
        $skeletons = New-Object "Microsoft.Kinect.Skeleton[]" $sensor.SkeletonStream.FrameSkeletonArrayLength
        Write-Host "Connected to Kinect sensor: $($sensor.UniqueKinectId)"
      }
    }

    if (-not $sensor) {
      Start-Sleep -Milliseconds 30
      continue
    }

    $depthFrame = $null
    $skeletonFrame = $null

    try {
      $depthFrame = $sensor.DepthStream.OpenNextFrame(5)
      $skeletonFrame = $sensor.SkeletonStream.OpenNextFrame(0)

      if ($skeletonFrame) {
        $skeletonFrame.CopySkeletonDataTo($skeletons)
      }

      if ($depthFrame) {
        $depthFrame.CopyDepthImagePixelDataTo($depthPixels)
        $players = Get-TrackedPlayers $skeletons
        $controls = Get-ControlSignals $players
        $pointCloud = Build-PointCloud $sensor $depthPixels $depthWidth $depthHeight $Stride $MinDepthMm $MaxDepthMm
        $latestSnapshot = New-BridgeSnapshot $true $null $pointCloud $players $controls
      } elseif (-not $latestSnapshot.connected) {
        $latestSnapshot = New-BridgeSnapshot $false "Waiting for depth frames from Kinect." @() @() $latestSnapshot.controls
      }
    } catch {
      $latestSnapshot = New-BridgeSnapshot $false $_.Exception.Message @() @() $latestSnapshot.controls

      if ($sensor) {
        try {
          $sensor.Stop()
        } catch {
        }
        $sensor = $null
      }
    } finally {
      if ($depthFrame) {
        $depthFrame.Dispose()
      }

      if ($skeletonFrame) {
        $skeletonFrame.Dispose()
      }
    }
  }
} finally {
  if ($sensor) {
    try {
      $sensor.Stop()
    } catch {
    }
  }

  $listener.Stop()
}
