<h4>Ke sčítání průletů v budce používáme vlastní přístroj postavený na těchto dostupných komponentech:</h4>
<ul>
 	<li><a href="https://www.aliexpress.com/item/1005006109082351.html">ESP32-C3 Super Mini</a></li>
 	<li><a href="https://www.aliexpress.com/item/1005007508685885.html">LM2596S Step Down Module DC to DC </a></li>
 	<li><a href="https://www.aliexpress.com/item/1005006385368806.html">IR Transmitter and Receiver set</a></li>
</ul>
<h1>HW</h1>
<ol>
 	<li>ESP32 Super Mini
<ol>
 	<li>Jedná se o levnou vývojovou desku. Tato deska řídí všechny senzory a získává jejich data. Data následně zpracovává a díky vestavěné WiFi odesílá na náš server k ukládání, spuštění nahrávání kamer a zobrazení na tomto webu.</li>
 	<li>Deska vysílá pomocí IR Transmitter modulu světlo modulované na frekvenci 38khz. Následně zpracovává data z IR Receiver modulu a detekuje přerušení světelného paprsku. Tyto přerušení vyhodnocuje jako ne/platná na základě časového limitu. Tj. ignoruje každou další detekci v limitu 1s od poslední.</li>
 	<li>Vyhodnocené záznamy o detekci jsou následně odeslané prostřednictvím MQTT brokeru (Mosquitto) do našeho Python skriptu.</li>
</ol>
</li>
 	<li>Step Down Module
<ol>
 	<li>Protože MCU funguje v rozmezí 3.3-5V, musíme snížit napájecí napětí z 12V na logickou úroveň 3.3V</li>
</ol>
</li>
 	<li>IR moduly
<ol>
 	<li>IR Transmitter module vysílá tzv. infračervené světlo. V našem případě modulované na frekvenci 38khz ke snadnějšímu oddělení od ostatních zdrojů IR (slunce). Toto světlo není pro většinu živočichů (vč. ptáku) viditelné.</li>
 	<li>IR Receiver module toto modulované světlo  přijímá a do naší řídící desky odešle pulz při začátku detekce tohoto světla.</li>
 	<li>Díky tomuto principu vytváříme tzv. infračervenou bránu.</li>
</ol>
</li>
</ol>
<h1>SW</h1>
<ol>
 	<li>MQTT broker
<ol>
 	<li>Na našem serveru je spuštěný tzv. MQTT broker (server). Jedná se o protokol často využívaný právě IoT zařízeními.</li>
</ol>
</li>
 	<li>Python server pro logování dat a sprostředování dat pro web.
<ol>
 	<li>Tento server je pomocí mqtt clienta připojený k mqtt brokeru a čeká na zprávy od IoT. V případě, že přijde zpráva o detekci, přidá ji do existujícího CSV souboru. Zde uloží čas (timesnap) detekce a DID (Device ID) zařízení, z kterého detekce přišla.</li>
 	<li>Náš web se pak k tomuto serveru připojuje pomocí SocketIO. Web může zažádat o data historie a zároveň jsou zde oznamovány i živé eventy z IoT</li>
</ol>
</li>
 	<li>Python NVR
<ol>
 	<li>NVR je tak připojené k mqtt brokeru a čeká na detekci od IoT. Naše NVR taky využívá opensource nástroj ffmpeg k jeho funkčnosti.</li>
 	<li>NVR neustále přepisuje 15s pre-buffer v RAM. V momentě detekce přestaneme mazat staré ts soubory v RAM a jen připisujeme nové po dobu 15s po detekci. Po detekci TS segmenty zkopírujeme na disk a vytvoříme k nim m3u8 playlist, meta file obsahující informace o DID i timesnapu detekce a náhledový obrázek JPG. Pro možnost stahování vytvoříme i MP4 soubor.</li>
 	<li>Kvůli rychlosti domácího internetu všechny soubory po vytvoření ihned přesouváme na Wedos hsoting, aby se video při přehrávání nesekalo.</li>
</ol>
</li>
</ol>
<h1>Streamování na YT</h1>
Ke streamování využíváme opensource CLI nástroj ffmpeg.

&nbsp;
