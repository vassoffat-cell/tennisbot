UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
mkdir -p probe/js
for c in 'pages/tennis/events/%5BmatchUrl%5D-5970e4b920d01871.js' 'pages/_app-a2eb5ff7dee36d8f.js' 'pages/feed-d11b5f2066450e8a.js' 1074-00a3087a2954ace7.js 739-4a3923a2c4da6b57.js 5292-54bbaa0859faeaf2.js 7561-93b9e7caeed4dfb7.js 9065-0bd0bff564ffb53b.js 8759-e8164fc21f1003f0.js 3491-59acb8a67a14d788.js 2453-b8d75ebf06372ef1.js 1448-599129c038df451b.js 6744-75ea529972d5db55.js; do
  n=$(echo $c | tr '/%' '__'); curl -sL -A "$UA" -o probe/js/$n "https://nb-bet.com/_next/static/chunks/$c"; done
grep -ohE '["`][^"`]{0,60}(api|Api|API)[^"`]{0,80}["`]' probe/js/* | sort -u | head -150 > probe/api_strings.txt
grep -ohE 'app\.nb-bet\.com[^"`]{0,80}' probe/js/* | sort -u > probe/app_hosts.txt
