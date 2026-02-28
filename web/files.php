<?php
header('Content-Type: application/json');

$dir = __DIR__ . '/nvr/m3u8';
$result = [];

// GET parametry
$tstart = isset($_GET['tstart']) ? (int)$_GET['tstart'] : null;
$tend   = isset($_GET['tend'])   ? (int)$_GET['tend']   : null;
$nest   = isset($_GET['nest'])   ? trim($_GET['nest'])   : null;  // filtr podle did

if (!is_dir($dir)) {
    echo json_encode(["error" => "Directory not found"]);
    exit;
}

$files = glob($dir . '/*.meta');
foreach ($files as $file) {
    $json = file_get_contents($file);
    $data = json_decode($json, true);
    if (!$data) continue;

    $timestamp   = $data['timestamp']   ?? null;
    $did         = $data['did']         ?? null;
    $stream_type = $data['stream_type'] ?? null;

    if (!$timestamp || !$did || !$stream_type) continue;

    $timestamp = (int)$timestamp;

    // filtr podle času
    if ($tstart !== null && $timestamp < $tstart) continue;
    if ($tend   !== null && $timestamp > $tend)   continue;

    // filtr podle did (nest)
    if ($nest !== null && $did !== $nest) continue;

    $videoName = basename($file, '.meta');

    if (!isset($result[$timestamp]))        $result[$timestamp] = [];
    if (!isset($result[$timestamp][$did]))  $result[$timestamp][$did] = [];

    $result[$timestamp][$did][$stream_type] = $videoName;
}

krsort($result);
echo json_encode($result, JSON_PRETTY_PRINT);