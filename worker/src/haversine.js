/**
 * haversine.js
 * ------------
 * Great-circle distance between two lat/lng points, in miles. Used to
 * filter maintainer-curated events (see local_sources.yaml) down to
 * whatever radius a visitor's travel-time preference implies.
 */

const EARTH_RADIUS_MILES = 3958.8;

function toRadians(deg) {
  return (deg * Math.PI) / 180;
}

export function distanceMiles(a, b) {
  const dLat = toRadians(b.lat - a.lat);
  const dLng = toRadians(b.lng - a.lng);
  const lat1 = toRadians(a.lat);
  const lat2 = toRadians(b.lat);

  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  const c = 2 * Math.asin(Math.min(1, Math.sqrt(h)));

  return EARTH_RADIUS_MILES * c;
}
