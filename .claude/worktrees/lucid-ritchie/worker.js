// Cloudflare Worker — paste this into the worker editor at dash.cloudflare.com
// Scrapes ski49n.com/mountain-info/expanded-conditions and returns JSON

addEventListener('fetch', event => {
  event.respondWith(handleRequest(event.request))
})

async function handleRequest(request) {
  const headers = {
    'Access-Control-Allow-Origin': '*',
    'Content-Type': 'application/json'
  }

  if (request.method === 'OPTIONS') {
    return new Response(null, { headers })
  }

  try {
    const res = await fetch('https://www.ski49n.com/mountain-info/expanded-conditions', {
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; ski49n-proxy/1.0)' }
    })
    const html = await res.text()

    // Extract <h4>label</h4><h3>value</h3> from a section of HTML
    function boxVal(section, label) {
      const esc = label.replace(/[.*+?^${}()|[\]\\\/]/g, '\\$&')
      const re = new RegExp('<h4>\\s*' + esc + '\\s*<\\/h4>\\s*<h3>([^<]*)<\\/h3>', 'i')
      const m = section ? section.match(re) : null
      return m ? m[1].replace(/&deg;/g, '°').replace(/&quot;/g, '"').trim() : ''
    }

    // Strip trailing inch mark from snowfall/depth values
    const inchVal = (sec, lbl) => boxVal(sec, lbl).replace(/["\u201d]$/, '')

    // Split HTML at the "Weather Conditions" heading to get two sections
    const parts = html.split(/<h3>\s*Weather Conditions\s*<\/h3>/i)
    const snowHtml    = parts[0] || ''
    const weatherHtml = parts[1] || ''

    // Split sections into rows
    const snowRows    = snowHtml.split(/<div\s+class="row">/i)
    const weatherRows = weatherHtml.split(/<div\s+class="row">/i)

    // Summit rows
    const summitSnow    = snowRows.find(r => r.includes('<h3>Summit</h3>'))    || ''
    const summitWeather = weatherRows.find(r => r.includes('<h3>Summit</h3>')) || ''

    // Updated timestamp: "Last updated: 04/02/26 at 5:38am"
    const updMatch = html.match(/Last updated:\s*([\d\/]+)\s+at\s+([\d:apm]+)/i)
    const updated  = updMatch ? `${updMatch[1]} ${updMatch[2]}` : ''

    const totals = {
      '12h':        inchVal(summitSnow, '12 Hours'),
      '24h':        inchVal(summitSnow, '24 Hours'),
      '48h':        inchVal(summitSnow, '48 Hours'),
      '72h':        inchVal(summitSnow, '72 Hours'),
      summitDepth:  inchVal(summitSnow, 'Snow Depth'),
      updated
    }

    const summit = {
      temp:          boxVal(summitWeather, 'Temperature').replace(/°$/, ''),
      wind:          boxVal(summitWeather, 'Wind'),
      visibility:    boxVal(summitWeather, 'Visibility'),
      precipitation: boxVal(summitWeather, 'Precipitation'),
      conditions:    boxVal(summitWeather, 'Current Weather')
    }

    // Special Notes: <h4>Special Notes</h4><h5>text</h5>
    const snMatch = html.match(/<h4>\s*Special Notes\s*<\/h4>\s*<h5>([^<]*)<\/h5>/i)
    const specialNotes = snMatch ? snMatch[1].trim() : ''

    return new Response(JSON.stringify({ totals, summit, forecast: {}, specialNotes }), { headers })
  } catch (e) {
    return new Response(JSON.stringify({ error: e.message }), { headers, status: 500 })
  }
}
