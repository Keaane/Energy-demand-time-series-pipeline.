// Run from the project root: mongosh "$MONGODB_URI" --file mongodb/setup.js
const fs = require("fs");
const validator = JSON.parse(fs.readFileSync("mongodb/schema.json", "utf8"));
const database = db.getSiblingDB(process.env.MONGODB_DATABASE || "energy_pipeline");
const collectionName = process.env.MONGODB_COLLECTION || "hourly_records";
const options = { validator, validationLevel: "strict", validationAction: "error" };

if (database.getCollectionNames().includes(collectionName)) {
  database.runCommand({ collMod: collectionName, ...options });
} else {
  database.createCollection(collectionName, options);
}
database.getCollection(collectionName).createIndex(
  { timestamp: 1 },
  { unique: true, name: "timestamp_unique" }
);
