function asObject(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return value;
}

function safeArray(value) {
  return Array.isArray(value) ? value : [];
}

function isUndefinedLike(value) {
  if (value === null || value === undefined) return true;
  if (typeof value !== 'string') return false;
  const text = value.trim().toLowerCase();
  return text === 'pydanticundefined' || text === 'undefined' || text === 'ellipsis';
}

function placeholderValueForType(typeName) {
  const kind = String(typeName || '').toLowerCase();
  if (kind.includes('bool')) return true;
  if (kind.includes('int') || kind.includes('float') || kind.includes('number') || kind.includes('decimal')) return 1;
  if (kind.includes('list') || kind.includes('array') || kind.includes('set') || kind.includes('tuple')) return ['example'];
  if (kind.includes('dict') || kind.includes('map') || kind.includes('object') || kind.includes('json')) return { sample: 'value' };
  if (kind.includes('date')) return '2026-01-01';
  return 'example';
}

function cloneTemplateValue(value) {
  if (value === null || value === undefined) return value;
  if (typeof structuredClone === 'function') return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
}

function routeKey(serviceKey, method, path) {
  return `${serviceKey}|${method}|${path}`;
}

function isBodyMethod(method) {
  return method === 'POST' || method === 'PUT' || method === 'PATCH';
}

function pathRuleForRoute(route) {
  const path = String(route?.path || '').toLowerCase();
  if (path.includes('/search')) {
    return {
      summary: 'Max template for search endpoint parameters.',
      body: {
        query: 'example search',
        domains: ['feeds', 'reference', 'library', 'rag', 'scrape', 'graph'],
        since: '2026-01-01',
        until: '2026-12-31',
        mode: 'keyword',
        min_match: 0.4,
        limit: 20,
      },
      query: {
        q: 'example search',
        limit: 20,
      },
    };
  }
  if (path.includes('/full-text')) {
    return {
      summary: 'Max template for full-text/artifact retrieval endpoints.',
      body: {
        refid: 'reference_article|title=Artificial%20intelligence',
      },
    };
  }
  if (path.includes('/sentence')) {
    return {
      summary: 'Max template for sentence lookup endpoints.',
      body: {
        locator: 'reference/default/1',
      },
    };
  }
  return null;
}

const CURATED_EXACT = {
  'korestack|POST|/api/endpoints/request': {
    summary: 'Max template for endpoint proxy request payload.',
    body: {
      method: 'POST',
      url: 'http://127.0.0.1:19603/api/search',
      content_type: 'application/json',
      body: '{"query":"example search","domains":["feeds","reference"],"mode":"keyword","limit":20}',
    },
  },
  'korefeed|GET|/api/search': {
    summary: 'Max template for feed search query params (GET endpoint; body not used).',
    query: {
      q: 'example search',
      limit: 20,
      full: true,
      since: '2026-01-01',
      until: '2026-12-31',
    },
  },
};

function templateFromDeclaredBody(route) {
  const bodyParams = safeArray(route?.body_params);
  if (!bodyParams.length) return null;

  const wrappedModel = bodyParams.length === 1
    && ['req', 'request', 'body', 'payload'].includes(String(bodyParams[0].name || '').toLowerCase())
    && String(bodyParams[0].type || '').toLowerCase().includes('request');

  if (wrappedModel) {
    const byPath = pathRuleForRoute(route);
    if (byPath?.body) {
      return {
        body: cloneTemplateValue(byPath.body),
        summary: `${byPath.summary} (model payload fallback)`,
      };
    }
    return {
      body: {
        example: 'value',
        note: 'Model fields are not exposed in this manifest yet',
      },
      summary: `Max fallback for model payload (${bodyParams[0].name}).`,
    };
  }

  const body = {};
  const required = [];
  const optional = [];
  for (const param of bodyParams) {
    body[param.name] = !isUndefinedLike(param.default)
      ? param.default
      : placeholderValueForType(param.type);
    if (param.required) required.push(param.name);
    else optional.push(param.name);
  }

  const details = [];
  if (required.length) details.push(`Required: ${required.join(', ')}`);
  if (optional.length) details.push(`Optional: ${optional.join(', ')}`);
  return {
    body,
    summary: `Max template from declared body params. ${details.join(' | ')}`.trim(),
  };
}

function templateFromDeclaredQuery(route) {
  const queryParams = safeArray(route?.query_params);
  if (!queryParams.length) return null;
  const query = {};
  for (const param of queryParams) {
    query[param.name] = !isUndefinedLike(param.default)
      ? param.default
      : placeholderValueForType(param.type);
  }
  return {
    query,
    summary: 'Max query template from declared query params (body not used).',
  };
}

function buildTemplateForRoute(route, method) {
  const pathRule = pathRuleForRoute(route);
  if (isBodyMethod(method)) {
    if (pathRule?.body) {
      return {
        bodyText: JSON.stringify(pathRule.body, null, 2),
        templateText: JSON.stringify(pathRule.body, null, 2),
        queryParams: null,
        summary: pathRule.summary,
      };
    }
    const declaredBody = templateFromDeclaredBody(route);
    if (declaredBody?.body) {
      return {
        bodyText: JSON.stringify(declaredBody.body, null, 2),
        templateText: JSON.stringify(declaredBody.body, null, 2),
        queryParams: null,
        summary: declaredBody.summary,
      };
    }
    return {
      bodyText: '{}',
      templateText: JSON.stringify({ body: {} }, null, 2),
      queryParams: null,
      summary: 'No body parameters declared. Template uses an empty JSON object.',
    };
  }

  if (pathRule?.query) {
    return {
      bodyText: '',
      templateText: JSON.stringify({ query_params: pathRule.query }, null, 2),
      queryParams: cloneTemplateValue(pathRule.query),
      summary: `${pathRule.summary} (query template)`,
    };
  }
  const declaredQuery = templateFromDeclaredQuery(route);
  if (declaredQuery?.query) {
    return {
      bodyText: '',
      templateText: JSON.stringify({ query_params: declaredQuery.query }, null, 2),
      queryParams: declaredQuery.query,
      summary: declaredQuery.summary,
    };
  }
  return {
    bodyText: '',
    templateText: JSON.stringify({ query_params: {} }, null, 2),
    queryParams: {},
    summary: 'No query params declared. This endpoint can be called as-is.',
  };
}

function templateFromDefinition(definition) {
  if (definition.body) {
    return {
      bodyText: JSON.stringify(definition.body, null, 2),
      templateText: JSON.stringify(definition.body, null, 2),
      queryParams: null,
      summary: definition.summary || 'Max template preset.',
    };
  }
  if (definition.query) {
    return {
      bodyText: '',
      templateText: JSON.stringify({ query_params: definition.query }, null, 2),
      queryParams: cloneTemplateValue(definition.query),
      summary: definition.summary || 'Max query template preset.',
    };
  }
  return {
    bodyText: '',
    templateText: '',
    queryParams: null,
    summary: definition.summary || 'No template content defined.',
  };
}

export function createTemplateCatalog(catalog) {
  const exact = {};
  const services = safeArray(asObject(catalog).services);
  for (const service of services) {
    const serviceKey = String(service?.key || '').trim();
    const routes = safeArray(asObject(service?.manifest).routes);
    for (const route of routes) {
      if (route?.kind !== 'api') continue;
      const path = String(route?.path || '').trim();
      for (const method of safeArray(route?.methods)) {
        const key = routeKey(serviceKey, method, path);
        exact[key] = buildTemplateForRoute(route, method);
      }
    }
  }

  for (const [key, definition] of Object.entries(CURATED_EXACT)) {
    exact[key] = templateFromDefinition(definition);
  }

  return { exact };
}

export function templateForRoute(templateCatalog, service, route) {
  const catalog = asObject(templateCatalog);
  const exact = asObject(catalog.exact);
  for (const method of safeArray(route?.methods)) {
    const key = routeKey(String(service?.key || ''), method, String(route?.path || ''));
    if (Object.prototype.hasOwnProperty.call(exact, key)) {
      return exact[key];
    }
  }
  return buildTemplateForRoute(route, safeArray(route?.methods)[0] || 'GET');
}
