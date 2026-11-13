export async function fetchApi(endpoint) {
  const response = await fetch(endpoint, {
    headers: { 'Accept': 'application/json' }
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
  return await response.json()
}
