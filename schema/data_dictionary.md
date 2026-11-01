# YottaFlex — Warehouse Slotting Data Dictionary

All IDs are deterministic, zero-padded and immutable. `PK` means unique primary key;
`FK` means the value must exist in the referenced entity. Other fields are not
unique unless stated. Units are embedded in field names (`cm`, `kg`, `cm3`, `m`,
`minutes`). Dates are ISO `YYYY-MM-DD`, timestamps ISO 8601. Seed: `20261022`
(fixed; scale parameterized).

## Cardinality summary

| Relationship | Cardinality | Note |
|---|---|---|
| Warehouse 1:N Zone | 1:N | every zone belongs to exactly one warehouse |
| Zone 1:N Location | 1:N | location ID embeds zone id |
| SKU 1:1 SKUProfile | 1:1 | profile optional in theory, required here |
| Warehouse 1:N SKU | 1:N | SKU catalogued per warehouse (assortment) |
| Order N:M SKU (via OrderLine) | N:M | bridge = OrderLine |
| Order 1:N OrderLine | 1:N | |
| OrderLine 1:N PickEvent | 1:N | each pick attempt logged |
| SKU 1:N Replenishment | 1:N | |
| Location 1:N SlotAssignment | 1:1 active in valid state | historical/rejected rows may repeat; labelled duplicate exception is deliberate |
| SKU 1:N SlotAssignment | 1:N | history retained (lifecycle states) |
| Supplier 1:N InboundShipment | 1:N | each shipment has one supplier |
| Carrier 1:N InboundShipment | 1:N | each shipment has one carrier |
| Warehouse 1:N InboundShipment | 1:N | inbound shipment belongs to one warehouse |
| InboundShipment 1:N InboundEvent | 1:N | append-only shipment status history |

Lifecycle states:
- `SlotAssignment.status`: `active`, `rejected`, or `retired`; reassignment preserves the old row and creates a new effective-dated active row.
- `AssignmentEvent`: activation, deactivation, retirement, reassignment activation, rejection, and exception detection are append-only transitions.
- `Order.status`: generated `open`, `picking`, or `shipped`; `cancelled` is reserved but not generated.
- `Replenishment.status`: generated `pending`, `in_progress`, or `completed`; `blocked` is reserved but not generated.

---

## Warehouse (`warehouse.csv`)
| field | type | req | allowed/values | validation |
|---|---|---|---|---|
| warehouse_id | str | Y | `WH-###` | PK, regex `^WH-\d{3}$` |
| name | str | Y | `Warehouse <city>` | unique |
| city, country | str | Y | free text | non-empty |
| region | enum | Y | NA-EAST, NA-WEST, NA-CENTRAL, EU, APAC | |
| opened_date | date | Y | 2000-01-01..2025-12-31 | <= today |
| — | — | — | — | Location totals are reported in manifest/metrics, not stored on this row. |

## Zone (`zone.csv`)
| field | type | req | values | validation |
|---|---|---|---|---|
| zone_id | str | Y | `ZN-<wh#>-<z>` e.g. `ZN-001-A` | PK |
| warehouse_id | str | Y | FK warehouse | FK integrity |
| zone_type | enum | Y | AMBIENT, COLD, FROZEN, HAZMAT, BULK | |
| hazard_segregation | enum | Y | NONE, FLAMMABLE, CORROSIVE, TOXIC | dedicated hazmat compartments match hazardous SKU class |
| storage_temp | enum | Y | AMBIENT, CHILLED, FROZEN | chilled/frozen SKUs use matching temperature-controlled zones; hazmat has matching temperature compartments |
| blocked_aisle | str | Y | aisle ID such as A01 | one blocked aisle ID per zone |
| aisle_count | int | Y | 3..5 | location aisle IDs stay within this count |

## Location (`location.csv`)
| field | type | req | values | validation |
|---|---|---|---|---|
| location_id | str | Y | `LOC-<zone-suffix>-<seq6>` per spec `LOC-<zone-id>-000001` | PK |
| zone_id | str | Y | FK zone | FK |
| warehouse_id | str | Y | FK warehouse, denormalized | FK |
| loc_type | enum | Y | SHELF, PALLET, FLOOR, COOLER, FREEZER, HAZ_CABINET | compatible with zone_type |
| zone_type | enum | Y | copied from parent Zone | must match parent zone |
| aisle | str | Y | A01..A05 | within parent aisle_count |
| capacity_volume_cm3 | int | Y | >0 | |
| max_weight_kg | num | Y | >0 | |
| length_cm | int | Y | >0 | physical dimension |
| width_cm | int | Y | >0 | physical dimension |
| height_cm | int | Y | >0 | physical dimension |
| rack_level | int | Y | 1..5 | |
| x_coord, y_coord | int | Y | meters from dock | |
| blocked | bool | Y | 0/1 | generated as 0; aisle closures are represented on Zone |
| occupancy_status | enum | Y | EMPTY, OCCUPIED | changes to OCCUPIED after an assignment |

## SKU (`sku.csv`)
| field | type | req | values | validation |
|---|---|---|---|---|
| sku_id | str | Y | `SKU-\d{6}` | PK |
| warehouse_id | str | Y | FK warehouse | FK |
| name | str | Y | | |
| category | enum | Y | FOOD, BEVERAGE, PHARMA, CHEMICAL, ELECTRONICS, APPAREL, HOME | |
| hazard_class | enum | Y | NONE, FLAMMABLE, CORROSIVE, TOXIC | |
| temp_class | enum | Y | AMBIENT, CHILLED, FROZEN | |
| unit_weight_kg | num | Y | 0.01..500 | |
| unit_volume_cm3 | int | Y | >0 | derived from length*width*height |
| length_cm | int | Y | >0 | physical dimension |
| width_cm | int | Y | >0 | physical dimension |
| height_cm | int | Y | >0 | physical dimension |
| oversized | bool | Y | derived: volume > pallet slot volume | |
| — | — | — | — | Velocity class is stored on SKUProfile, not SKU. |

## SKUProfile (`sku_profile.csv`)
| field | type | req | values | validation |
|---|---|---|---|---|
| profile_id | str | Y | `PRF-\d{6}` | PK |
| sku_id | str | Y | FK SKU, unique | 1:1 |
| pick_frequency_30d | int | Y | sum of SKU qty in the 30 days ending on latest ship date | reconciles to OrderLine |
| avg_daily_picks | num | Y | pick_frequency_30d / 30 | recomputes from frequency |
| min_stock_qty | int | Y | >=0 | |
| max_stock_qty | int | Y | >= min | |
| preferred_zone_types | str | Y | `;`-joined enums | compatible with hazard/temp |
| incompat_with | str | Y | `;`-joined category or hazard classes | symmetric pairs |
| velocity_class | enum | Y | FAST, MEDIUM, SLOW | FAST top 20%, MEDIUM next 30%, SLOW remainder by nonzero demand rank |
| abc_class | enum | Y | A/B/C | same ranked cutoffs; zero-demand/new SKUs are C |

## Order (`order.csv`)
| field | type | req | values | validation |
|---|---|---|---|---|
| order_id | str | Y | `ORD-2026-\d{6}` | PK |
| warehouse_id | str | Y | FK | |
| customer_ref | str | Y | `CUST-\d{5}` | |
| order_date | date | Y | <= ship_date | ordering |
| ship_date | date | Y | >= order_date | |
| status | enum | Y | lifecycle above | transitions valid |
| channel | enum | Y | RETAIL, ECOM, WHOLESALE | |
| peak_period | enum | Y | NORMAL, PEAK | orders are sampled more densely in the final 51-day synthetic peak window; EVE picks are more likely during PEAK |

## OrderLine (`order_line.csv`)
| field | type | req | values | validation |
|---|---|---|---|---|
| line_id | str | Y | `<order_id>-L<seq3>` | PK |
| order_id | str | Y | FK order | |
| sku_id | str | Y | FK sku | |
| qty | int | Y | 1..999 | |
| pick_freq_weight | int | Y | derived | |

## PickEvent (`pick_event.csv`)
| field | type | req | values | validation |
|---|---|---|---|---|
| pick_id | str | Y | `PICK-<order_id>-<seq3>` | PK |
| order_id | str | Y | FK | |
| line_id | str | Y | FK order_line | |
| sku_id | str | Y | FK | |
| location_id | str | Y | FK location (from SlotAssignment) | |
| pick_ts | timestamp | Y | within ship window shift | |
| shift | enum | Y | DAY, EVE, NIGHT | peak EVE |
| travel_distance_m | num | Y | Dijkstra shortest path over the generated warehouse aisle graph; blocked aisles are excluded from transit | >=0 |
| path_status | enum | Y | OK, BLOCKED_AISLE | BLOCKED_AISLE = anomaly |

## Replenishment (`replenishment.csv`)
| field | type | req | values | validation |
|---|---|---|---|---|
| replenishment_id | str | Y | `REPL-\d{6}` | PK |
| sku_id | str | Y | FK | |
| location_id | str | Y | FK assigned location | |
| qty | int | Y | >0 | |
| labor_minutes | num | Y | derived qty/velocity | |
| required_date, required_shift | date/enum | Y | requested date and DAY/EVE/NIGHT shift | original requested bucket |
| scheduled_date, scheduled_shift | date/enum | Y | allocated date and shift | next available warehouse shift |
| priority_class | enum | Y | HIGH, MEDIUM, LOW | FAST, MEDIUM, SLOW velocity respectively |
| reschedule_count | int | Y | number of shift steps moved | 0 for on-time work |
| scheduled_shift_labor_minutes | num | Y | total allocated load in scheduled warehouse/date/shift | <= 480 |
| remaining_shift_capacity_minutes | num | Y | capacity left after scheduled allocation | 480 - scheduled shift load |
| scheduled_labor_minutes | num | Y | full task labor allocated to one shift | equals labor_minutes |
| deferred_labor_minutes | num | Y | task labor delayed from requested bucket | zero if not rescheduled; full labor if delayed |
| shift_total_labor_minutes | num | Y | original requested workload per warehouse/date/shift | same value on original bucket tasks |
| shift_capacity_minutes | num | Y | 480 | fixed per warehouse/date/shift |
| schedule_decision | enum | Y | SCHEDULED, RESCHEDULED | records whether allocation moved |
| shift | enum | Y | DAY, EVE, NIGHT | original requested shift (legacy field) |
| status | enum | Y | lifecycle | |
| over_labor_capacity | bool | Y | derived flag | 1 when any task minutes are deferred |

## SlotAssignment (`slot_assignment.csv`)
| field | type | req | values | validation |
|---|---|---|---|---|
| slot_id | str | Y | `SLOT-2026-\d{6}` | PK |
| sku_id | str | Y | FK | |
| location_id | str | Y | FK | |
| warehouse_id | str | Y | FK | |
| score | num | Y | weighted candidate ranking score | 0..100; eligible slots only; velocity 30%, dock distance 25%, capacity utilization 15%, replenishment efficiency 15%, operational fit 15%; rejected exceptions score 0 |
| decision | enum | Y | ASSIGNED, REJECTED_CAPACITY, REJECTED_COMPAT, DUPLICATE | exception decisions carry a ground-truth label |
| status | enum | Y | active, rejected, retired | at most 1 valid ACTIVE assignment per location; deliberate exceptions are labelled |
| effective_from, effective_to | date | Y | from<=to | effective dating |

## AssignmentEvent (`assignment_event.csv`)
| field | type | req | values | validation |
|---|---|---|---|---|
| event_id | str | Y | `AEVT-\d{6}` | PK |
| slot_id | str | Y | FK SlotAssignment | FK integrity |
| sku_id, location_id, warehouse_id | str | Y | referenced assignment identifiers | must agree with referenced assignment |
| event_type | enum | Y | ASSIGNMENT_ACTIVATED, ASSIGNMENT_DEACTIVATED, ASSIGNMENT_RETIRED, REASSIGNMENT_ACTIVATED, REJECTED, EXCEPTION_DETECTED | describes append-only lifecycle event |
| from_status, to_status | str | optional/required | prior and resulting state | valid lifecycle transition; exception detection preserves the state |
| decision | enum | Y | SlotAssignment decision | equals referenced assignment decision |
| event_timestamp | timestamp | Y | ISO 8601 | event order is retained |
| effective_from, effective_to | date | Y | from<=to | effective dating |
| move_id | str | optional | `MOVE-2026-######` | links old and replacement slot events |
| related_slot_id | str | optional | FK SlotAssignment | paired slot for a move |

## ReplenishmentEvent (`replenishment_event.csv`)
| field | type | req | values | validation |
|---|---|---|---|---|
| event_id | str | Y | `REPLEVT-#######` | PK |
| replenishment_id | str | Y | FK Replenishment | FK integrity |
| event_type | enum | Y | REQUESTED, DEFERRED, RESCHEDULED | ordered lifecycle |
| from_status, to_status | str | Y | pending/deferred transitions | state transition audit |
| event_timestamp | timestamp | Y | ISO 8601 | chronological |
| from_date, from_shift, to_date, to_shift | date/enum | optional/required | allocation movement | final values match Replenishment |
| priority_class | enum | Y | HIGH, MEDIUM, LOW | same as replenishment task |

## Supplier, Carrier, InboundShipment, and InboundEvent

These supporting entities represent the supplier and carrier actors in the
workflow. All records are synthetic. InboundShipment links one supplier, one
carrier, one warehouse, and one SKU; InboundEvent preserves the shipment's
status transitions.

### Supplier (`supplier.csv`)
| field | type | req | allowed/values | validation |
|---|---|---|---|---|
| supplier_id | str | Y | `SUP-\d{6}` | PK |
| name | str | Y | synthetic supplier name | non-empty |
| country | str | Y | synthetic country value | non-empty |
| status | enum | Y | ACTIVE | |

### Carrier (`carrier.csv`)
| field | type | req | allowed/values | validation |
|---|---|---|---|---|
| carrier_id | str | Y | `CAR-\d{6}` | PK |
| name | str | Y | synthetic carrier name | non-empty |
| transport_mode | enum | Y | ROAD, AIR, RAIL, OCEAN | |
| status | enum | Y | ACTIVE | |

### InboundShipment (`inbound_shipment.csv`)
| field | type | req | allowed/values | validation |
|---|---|---|---|---|
| shipment_id | str | Y | `INB-2026-\d{6}` | PK |
| warehouse_id | str | Y | FK Warehouse | must match the SKU's warehouse |
| supplier_id | str | Y | FK Supplier | |
| carrier_id | str | Y | FK Carrier | |
| sku_id | str | Y | FK SKU | |
| qty, received_qty | int | Y | units | qty > 0; received_qty in 0..qty |
| booked_date, planned_arrival_date | date | Y | ISO date | booked <= planned arrival |
| status | enum | Y | BOOKED, IN_TRANSIT, RECEIVED | agrees with last shipment event |

### InboundEvent (`inbound_event.csv`)
| field | type | req | allowed/values | validation |
|---|---|---|---|---|
| event_id | str | Y | `INBEVT-\d{7}` | PK |
| shipment_id | str | Y | FK InboundShipment | |
| event_type | enum | Y | BOOKED, IN_TRANSIT, RECEIVED | |
| from_status, to_status | str | optional/required | shipment state transition | transitions must be valid and ordered |
| event_timestamp | timestamp | Y | ISO 8601 | nondecreasing within shipment |

## ground_truth.csv (separate; NEVER an input feature)
| field | values |
|---|---|
| record_id | ID of offending record |
| entity | Zone/Location/SlotAssignment/PickEvent/Replenishment/SKU |
| issue_type | enum | Y | CAPACITY_EXCEEDED, INCOMPATIBLE_COLOCATION, DUPLICATE_ACTIVE_SLOT, BLOCKED_AISLE_PICK, REPLEN_LABOR_EXCEEDED | validated against scenario records |
| source_ids | JSON array of strings | Y | source/parent IDs used to derive the case | each ID must exist in its referenced entity |
| affected_ids | JSON array of strings | Y | affected SKU/location/order IDs | each ID must exist in its referenced entity |
| reason | str | Y | rule-based explanation | non-empty; explains the violated rule |
| expected_decision | str | Y | correct operational response | non-empty |
| severity | enum | Y | LOW, MEDIUM, HIGH | |
| resolution_status | enum | Y | OPEN, REJECTED, REROUTED, RESCHEDULED | outcome/resolution state for metrics |
