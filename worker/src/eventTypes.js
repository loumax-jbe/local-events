/**
 * eventTypes.js
 * -------------
 * The same event_type taxonomy as classify.py's EVENT_TYPES — kept here
 * only to validate the `types` query param against a known list. Actual
 * classification happens once, in the Python pipeline, before events
 * ever reach events.json; the Worker just filters by whatever event_type
 * is already on each event.
 */

export const EVENT_TYPES = [
  "Concert",
  "Theater & Performing Arts",
  "Comedy",
  "Family & Kids",
  "Festival & Fair",
  "Sports",
  "Community & Civic",
  "School & Youth",
  "Film",
  "Other",
];
