<?php
/*
 * PetriKlar-Abrufhilfe (Relay) fuer die stuendlichen Messwert-Abrufe.
 *
 * Mehrere Behoerdenserver lehnen seit Oktober 2026 Anfragen von GitHub-Servern
 * ab, liefern dieselben oeffentlichen Daten aber an normale Webserver aus. Der
 * Messwerte-Lauf im Repository PetriKlar-Sensordaten schickt die Abrufe dieser
 * Server deshalb hierher; dieses Skript holt die Datei und reicht sie 1:1
 * zurueck.
 *
 * Schutz:
 *  - nur mit dem geheimen Schluessel (Header X-PetriKlar-Key). Gespeichert ist
 *    hier nur sein SHA-256-Wert in config.php, die nicht im Repository liegt.
 *  - nur die fest eingetragenen Behoerden-Hosts unten, nur http/https,
 *    Standardports, keine Weiterleitungen (die macht der Aufrufer selbst,
 *    damit auch das Weiterleitungsziel wieder geprueft wird).
 *  - Groessen- und Zeitlimit.
 *
 * Selbsttest (zeigt je Host, ob er von hier erreichbar ist):
 *   /relay/fetch.php?selftest=1   mit Header X-PetriKlar-Key
 */

declare(strict_types=1);

const RELAY_HOSTS = [
    // Oesterreich
    // data.ooe.gv.at leitet seit Oktober 2026 auf www.land-oberoesterreich.gv.at um.
    'data.ooe.gv.at', 'www.land-oberoesterreich.gv.at', 'hydro.tirol.gv.at', 'www.noe.gv.at', 'www.noel.gv.at',
    'info.ktn.gv.at', 'hydrographie.ktn.gv.at', 'egov.stmk.gv.at',
    'www.hydrografie.steiermark.at', 'www.salzburg.gv.at', 'vowis.vorarlberg.at',
    'ehyd.gv.at', 'gis.lfrz.gv.at',
    // Deutschland
    'www.gkd.bayern.de', 'www.nid.bayern.de', 'www.hnd.bayern.de',
    'wasserportal.berlin.de', 'undine.bafg.de',
    'lfu.brandenburg.de', 'mleuv-daten.brandenburg.de',
    'www.hydrologie.nrw.de', 'www.hlnug.de', 'app.hlnug.de',
    'www.wasser.sachsen.de', 'www.gewaessergueteonline.nlwkn.niedersachsen.de',
    'www.pegelonline.wsv.de', 'niz.baden-wuerttemberg.de', 'www.hvz.baden-wuerttemberg.de',
    'geodaten-wasser.rlp-umwelt.de', 'www2.bsh.de',
    'inovum-services.de', 'www.gewaesser-monitoring.de',
    // Schweiz und Niederlande
    'www.hydrodaten.admin.ch', 'data.bafu.admin.ch', 'lindas.admin.ch', 'ld.admin.ch',
    'environment.ld.admin.ch',
    'ddapi20-waterwebservices.rijkswaterstaat.nl', 'geo.rijkswaterstaat.nl',
    'rijkswaterstaatdata.nl', 'waterinfo.rws.nl',
    // Nachbarlaender, Mur und Donau ausserhalb DE/AT
    'opendata.chmi.cz', 'danepubliczne.imgw.pl', 'hubeau.eaufrance.fr', 'www.dati.lombardia.it',
    'afdj.ro', 'www.hidmet.gov.rs', 'hidro.dhz.hr', 'appd-bg.org',
    'www.arso.gov.si', 'vode.arso.gov.si',
];

const MAX_BYTES = 60 * 1024 * 1024;
const TIMEOUT_S = 50;
const FORWARD_REQUEST_HEADERS = [
    'accept', 'accept-language', 'content-type', 'cookie', 'referer', 'user-agent',
    'x-requested-with', 'faces-request', 'if-modified-since', 'if-none-match',
];
const FORWARD_RESPONSE_HEADERS = [
    'content-type', 'location', 'set-cookie', 'last-modified', 'etag',
    'content-disposition', 'retry-after',
];

function relay_fail(int $status, string $message): void
{
    if (!headers_sent()) {
        http_response_code($status);
        header('Content-Type: text/plain; charset=utf-8');
        header('X-PetriKlar-Relay: error');
        header('Cache-Control: no-store');
    }
    echo $message, "\n";
    exit;
}

function relay_request_headers(): array
{
    $headers = [];
    if (function_exists('getallheaders')) {
        foreach (getallheaders() as $name => $value) {
            $headers[strtolower((string)$name)] = (string)$value;
        }
    } else {
        foreach ($_SERVER as $key => $value) {
            if (strpos($key, 'HTTP_') === 0) {
                $headers[strtolower(str_replace('_', '-', substr($key, 5)))] = (string)$value;
            }
        }
        if (isset($_SERVER['CONTENT_TYPE'])) {
            $headers['content-type'] = (string)$_SERVER['CONTENT_TYPE'];
        }
    }
    return $headers;
}

$config_file = __DIR__ . '/config.php';
$config = is_file($config_file) ? include $config_file : [];
$expected = is_array($config) ? (string)($config['key_sha256'] ?? '') : '';
$incoming = relay_request_headers();
$key = $incoming['x-petriklar-key'] ?? '';
if ($expected === '' || strlen($expected) !== 64) {
    relay_fail(503, 'Relay nicht eingerichtet.');
}
if ($key === '' || !hash_equals($expected, hash('sha256', $key))) {
    relay_fail(403, 'Kein Zugriff.');
}
if (!function_exists('curl_init')) {
    relay_fail(500, 'PHP-Erweiterung curl fehlt.');
}

function relay_check_url(string $url): array
{
    $parts = parse_url($url);
    if (!is_array($parts) || empty($parts['host']) || empty($parts['scheme'])) {
        relay_fail(400, 'Ungueltige Adresse.');
    }
    $scheme = strtolower($parts['scheme']);
    $host = strtolower($parts['host']);
    if (!in_array($scheme, ['http', 'https'], true)) {
        relay_fail(400, 'Nur http und https.');
    }
    if (isset($parts['port']) && !in_array((int)$parts['port'], [80, 443], true)) {
        relay_fail(400, 'Nur Standardports.');
    }
    if (isset($parts['user']) || isset($parts['pass'])) {
        relay_fail(400, 'Keine Zugangsdaten in der Adresse.');
    }
    if (!in_array($host, RELAY_HOSTS, true)) {
        relay_fail(403, 'Host nicht freigegeben: ' . $host);
    }
    return [$scheme, $host];
}

if (isset($_GET['selftest'])) {
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    $out = ['php' => PHP_VERSION, 'hosts' => []];
    foreach (RELAY_HOSTS as $host) {
        $ch = curl_init('https://' . $host . '/');
        curl_setopt_array($ch, [
            CURLOPT_NOBODY => true, CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CONNECTTIMEOUT => 6, CURLOPT_TIMEOUT => 8,
            CURLOPT_USERAGENT => 'PetriKlar/1.0 (+https://www.petriklar.com)',
        ]);
        curl_exec($ch);
        $out['hosts'][$host] = [
            'status' => (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE),
            'error' => curl_error($ch),
            'seconds' => round((float)curl_getinfo($ch, CURLINFO_TOTAL_TIME), 2),
        ];
        curl_close($ch);
    }
    echo json_encode($out, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    exit;
}

$url = (string)($_GET['u'] ?? '');
relay_check_url($url);
$method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));
if (!in_array($method, ['GET', 'POST', 'HEAD'], true)) {
    relay_fail(405, 'Nur GET, POST und HEAD.');
}

$send = [];
foreach (FORWARD_REQUEST_HEADERS as $name) {
    if (isset($incoming[$name]) && $incoming[$name] !== '') {
        $send[] = $name . ': ' . str_replace(["\r", "\n"], '', $incoming[$name]);
    }
}
if (!isset($incoming['user-agent'])) {
    $send[] = 'user-agent: PetriKlar/1.0 (+https://www.petriklar.com)';
}
$send[] = 'expect:';

$state = ['status' => 0, 'headers' => [], 'sent' => false, 'bytes' => 0];

$ch = curl_init($url);
curl_setopt_array($ch, [
    CURLOPT_CUSTOMREQUEST => $method,
    CURLOPT_NOBODY => $method === 'HEAD',
    CURLOPT_HTTPHEADER => $send,
    CURLOPT_FOLLOWLOCATION => false,
    CURLOPT_CONNECTTIMEOUT => 15,
    CURLOPT_TIMEOUT => TIMEOUT_S,
    CURLOPT_ENCODING => '',
    CURLOPT_PROTOCOLS => CURLPROTO_HTTP | CURLPROTO_HTTPS,
    CURLOPT_SSL_VERIFYPEER => true,
    CURLOPT_HEADERFUNCTION => function ($ch, string $line) use (&$state): int {
        $trimmed = rtrim($line, "\r\n");
        if (preg_match('#^HTTP/\S+\s+(\d{3})#', $trimmed, $m)) {
            $state['status'] = (int)$m[1];
            $state['headers'] = [];
        } elseif ($trimmed !== '' && strpos($trimmed, ':') !== false) {
            [$name, $value] = explode(':', $trimmed, 2);
            $name = strtolower(trim($name));
            if (in_array($name, FORWARD_RESPONSE_HEADERS, true)) {
                $state['headers'][] = [$name, trim($value)];
            }
        }
        return strlen($line);
    },
    CURLOPT_WRITEFUNCTION => function ($ch, string $data) use (&$state): int {
        if (!$state['sent']) {
            relay_send_head($state);
        }
        $state['bytes'] += strlen($data);
        if ($state['bytes'] > MAX_BYTES) {
            return 0; // bricht den Abruf ab
        }
        echo $data;
        return strlen($data);
    },
]);
if ($method === 'POST') {
    curl_setopt($ch, CURLOPT_POSTFIELDS, (string)file_get_contents('php://input'));
}

function relay_send_head(array &$state): void
{
    $state['sent'] = true;
    http_response_code($state['status'] ?: 502);
    header('X-PetriKlar-Relay: ok');
    header('Cache-Control: no-store');
    header_remove('X-Powered-By');
    foreach ($state['headers'] as [$name, $value]) {
        header($name . ': ' . $value, $name !== 'set-cookie');
    }
    if (!array_filter($state['headers'], fn($h) => $h[0] === 'content-type')) {
        header('Content-Type: application/octet-stream');
    }
}

while (ob_get_level() > 0) {
    ob_end_flush();
}
$ok = curl_exec($ch);
$error = curl_error($ch);
curl_close($ch);

if (!$state['sent']) {
    if ($ok === false || $state['status'] === 0) {
        relay_fail(502, 'Abruf fehlgeschlagen: ' . $error);
    }
    relay_send_head($state); // leere Antwort, z. B. 304 oder Weiterleitung
}
