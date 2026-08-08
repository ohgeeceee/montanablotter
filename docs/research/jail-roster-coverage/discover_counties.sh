#!/usr/bin/env bash
# Try multiple URL candidates per county to find a working sheriff/jail page.
# Index what's known about the Montana jail landscape.

set -u
ROOT="/root/montanablotter/docs/research/jail-roster-coverage"
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
mkdir -p "$ROOT/probe"

# (slug  url1 url2 url3 ...) - mix of common Montana county URL patterns
declare -A COUNTIES=(
    [blaine]="https://www.blainecounty-mt.gov/sheriff https://blainecounty-mt.gov https://www.blainecountymt.gov"
    [carter]="https://www.cartercountymt.gov/161/Sheriff https://cartercountymt.gov https://www.co.carter.mt.us"
    [chouteau]="https://chouteaucountymt.gov https://www.chouteaucountymt.gov https://www.co.chouteau.mt.us"
    [daniels]="https://www.danielscountymt.gov/sheriff https://danielscountymt.gov https://www.danielscomt.us"
    [dawson]="https://www.dawsoncountymontana.com/sheriff https://dawsoncountymontana.com https://www.dawsoncountymt.com"
    [deer-lodge]="https://www.anacondadeerlodgecounty.com/sheriff https://anacondadeerlodgecounty.com https://www.adlcmt.gov https://www.deerlodgecountymt.com"
    [garfield]="https://www.garfieldcountymt.gov/sheriff https://garfieldcountymt.gov"
    [golden-valley]="https://www.goldenvalleymt.com https://goldenvalleymt.com https://www.goldenvalleycountymt.gov"
    [granite]="https://www.granitecountymt.gov https://granitecountymt.gov https://www.co.granite.mt.us"
    [judith-basin]="https://www.co.judith-basin.mt.us https://co.judith-basin.mt.us https://www.judithbasincountymt.gov"
    [liberty]="https://www.co.liberty.mt.gov https://www.libertycountymt.gov https://libertycountymt.gov"
    [mccone]="https://www.mcconecountymt.gov https://mcconecountymt.gov https://www.mccone.mt.gov"
    [mineral]="https://co.mineral.mt.us https://www.mineralcountymt.gov"
    [musselshell]="https://www.musselshellcounty.org https://musselshellcounty.org https://www.musselshellcountymt.gov"
    [petroleum]="https://www.petroleumcountymt.gov https://petroleumcountymt.gov"
    [phillips]="https://phillipscosheriff.com https://www.phillipscosheriff.com https://www.co.phillips.mt.us https://www.phillipscountymt.gov"
    [pondera]="https://www.ponderacountymt.gov https://ponderacountymt.gov https://ponderacountyjail.org"
    [powder-river]="https://www.powderivercountymt.gov https://powderivercountymt.gov https://www.powderrivercountymt.gov"
    [powell]="https://www.powellcountymt.gov https://powellcountymt.gov https://www.powellco.org"
    [prairie]="https://www.prairiecountymt.gov https://prairiecountymt.gov"
    [richland]="https://www.richland.org https://richland.org https://www.richlandcountymt.gov"
    [sheridan]="https://www.sheridancountymt.gov https://sheridancountymt.gov https://www.co.sheridan.mt.us"
    [sweet-grass]="https://www.sweetgrasscountymt.gov https://sweetgrasscountymt.gov https://www.co.sweetgrass.mt.us"
    [teton]="https://www.tetoncomt.gov https://tetoncomt.gov https://www.tetonmt.org https://www.co.teton.mt.us"
    [toole]="https://www.co.toole.mt.gov https://www.toolecountymt.gov https://toolecountymt.gov"
    [treasure]="https://www.treasurecountymt.gov https://treasurecountymt.gov"
    [wibaux]="https://www.wibauxcountymt.gov https://wibauxcountymt.gov"
)

probe_one() {
    local slug=$1 url=$2
    local out="$ROOT/probe/${slug}-$(echo "$url" | md5sum | head -c 8).html"
    local code size
    code=$(curl -s -o "$out" -w "%{http_code}" --max-time 12 -L -A "$UA" -k "$url" 2>/dev/null || echo "000")
    size=$(stat -c %s "$out" 2>/dev/null || echo 0)
    rm -f "$out"  # don't keep probe responses, just print
    echo "  $slug -> HTTP $code ($size bytes) $url"
    if [[ "$code" == "200" && $size -gt 5000 ]]; then
        echo "GOOD:$slug:$url"
    fi
}

for slug in "${!COUNTIES[@]}"; do
    IFS=' ' read -ra urls <<< "${COUNTIES[$slug]}"
    for url in "${urls[@]}"; do
        result=$(probe_one "$slug" "$url")
        if [[ "$result" == GOOD:* ]]; then
            # Save good URL
            good_url=$(echo "$result" | cut -d':' -f3-)
            # Append to a good-urls file
            echo "$slug|$good_url" >> "$ROOT/good_urls.txt"
            break
        fi
    done
done

echo "=== Good URLs ==="
cat "$ROOT/good_urls.txt"
