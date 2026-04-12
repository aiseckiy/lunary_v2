const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
};

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders });
  }

  const KASPI_TOKEN = Deno.env.get('KASPI_TOKEN') ?? '';
  const KASPI_SHOP_ID = Deno.env.get('KASPI_SHOP_ID') ?? '';
  const BASE = 'https://kaspi.kz/shop/api/v2';

  const headers = {
    'X-Auth-Token': KASPI_TOKEN,
    'Content-Type': 'application/vnd.api+json',
    'Accept': '*/*',
  };

  try {
    const body = await req.json();
    const { action, params = {} } = body;

    if (action === 'get_orders') {
      const url = new URL(`${BASE}/orders`);
      url.searchParams.set('page[number]', String(params.page ?? 0));
      url.searchParams.set('page[size]', String(params.size ?? 100));
      if (params.state) url.searchParams.set('filter[orders][state]', params.state);
      if (params.status) url.searchParams.set('filter[orders][status]', params.status);
      if (params.creationDateGe) url.searchParams.set('filter[orders][creationDate][$ge]', params.creationDateGe);
      if (params.creationDateLe) url.searchParams.set('filter[orders][creationDate][$le]', params.creationDateLe);

      const r = await fetch(url.toString(), { headers });
      const data = await r.json();
      return new Response(JSON.stringify({ success: true, data }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }

    if (action === 'get_order_entries') {
      const r = await fetch(`${BASE}/orders/${params.orderId}/entries`, { headers });
      const data = await r.json();
      return new Response(JSON.stringify({ success: true, data }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }

    if (action === 'update_stock') {
      const url = `${BASE}/masterdata/${KASPI_SHOP_ID}/offers/`;
      const payload = {
        data: [{
          type: 'offer',
          attributes: { availableQuantity: params.qty, skuCode: params.kaspi_sku }
        }]
      };
      const r = await fetch(url, { method: 'POST', headers, body: JSON.stringify(payload) });
      const data = await r.text();
      return new Response(JSON.stringify({ success: true, status: r.status, data }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }

    if (action === 'update_order_status') {
      const payload = {
        data: {
          type: 'orders',
          id: params.orderId,
          attributes: {
            code: params.code,
            status: params.status,
            ...(params.cancellationReason ? { cancellationReason: params.cancellationReason } : {})
          }
        }
      };
      const r = await fetch(`${BASE}/orders`, { method: 'POST', headers, body: JSON.stringify(payload) });
      const data = await r.json();
      return new Response(JSON.stringify({ success: true, data }), {
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      });
    }

    return new Response(JSON.stringify({ success: false, error: 'Unknown action' }), {
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });

  } catch (e) {
    return new Response(JSON.stringify({ success: false, error: e.message }), {
      status: 500,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  }
});
