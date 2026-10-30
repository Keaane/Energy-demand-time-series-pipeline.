// Run from the project root: mongosh "$MONGODB_URI" --file mongodb/queries.js
const database = db.getSiblingDB(process.env.MONGODB_DATABASE || "energy_pipeline");
const records = database.getCollection("hourly_records");
const start = ISODate("2018-12-31T00:00:00Z");
const end = ISODate("2018-12-31T03:00:00Z");

// 1. Latest observation in the historical dataset.
printjson(records.find({}, { _id: 0 }).sort({ timestamp: -1 }).limit(1).toArray());

// 2. Date range: start is inclusive; end is exclusive.
printjson(records.find(
  { timestamp: { $gte: start, $lt: end } },
  { _id: 0 }
).sort({ timestamp: 1 }).toArray());

// 3. Daily demand summary, grouped by UTC date.
printjson(records.aggregate([
  { $match: { timestamp: { $gte: ISODate("2018-12-30T00:00:00Z"), $lt: ISODate("2019-01-01T00:00:00Z") } } },
  { $group: {
    _id: { $dateToString: { date: "$timestamp", format: "%Y-%m-%d", timezone: "UTC" } },
    count: { $sum: 1 },
    avg_load_mw: { $avg: "$load_mw" },
    min_load_mw: { $min: "$load_mw" },
    max_load_mw: { $max: "$load_mw" }
  } },
  { $sort: { _id: 1 } },
  { $project: { _id: 0, date: "$_id", count: 1, avg_load_mw: 1, min_load_mw: 1, max_load_mw: 1 } }
]).toArray());
