"""navaudit1943_checks: the vehicle classes and the audit's own settings (not in the game's data, not in NavKit)."""
import re


GROUND = {'willys-mb', 'kurogane', 'm4-sherman', 'type97-chiha'}
BOATS = {'lcvp-landingcraft'}
AIRCRAFT = {'corsair', 'zero_fighter'}
STATIONARY = {'aa_bofors', '30cal_tripod', '30cal_windowmount'}
BOAT_REACH = 6.0          # m: an LCVP in the water is boarded from ground this close (it is about 11 m long)
VEHICLE_REACH = 4.0       # m (3-D, spawn point to walkable ground): the fallback for a vehicle whose blueprint gives no
                          # entry point (a vehicle is judged first by its PlayerEntryComponentData's EntryRadius, see
                          # vehicle_entries): half a vehicle plus a step, chosen here. Mounted guns without entry
                          # points use OBJECTIVE_REACH: the gunner stands at the gun
MOUNT_DROP = 2.0          # m: a mounted gun's gunner stands on a floor up to this far below the gun's spawn point
AIRBORNE = 3.0           # m: a spawn this far above its floor is in the air, not on the ground
USABLE_SHARE = 0.25       # a capture area with less of its land on the main network than this is listed
NEAR = 0.2                # near-miss band: within 20 % of a threshold
CLUSTER_CELL = 2.0        # m: rule-firing and difference clusters are grouped on this grid
PIER_RX = re.compile(r'pier|jetty|dock|landing_?stage|boardwalk|bridge', re.I)
