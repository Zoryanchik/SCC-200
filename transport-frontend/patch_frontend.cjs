const fs = require('fs');
const path = require('path');
const file = path.join('/Users/lty/Desktop/scc200/puppy/SCC-200/transport-frontend/src/components/map/MapViewMap.jsx');
let content = fs.readFileSync(file, 'utf8');

const target1 = `norm = safeNormalizeCoords(data && data.coords);
							try { console.debug('[map] leg-geometry selected route_tracks (cached)', { line: String(line), rid, coordsLen: norm.length }); } catch (e) { /* ignore */ }
							const color = busIconColor(marker.delayMinutes);
							setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: norm, color } : { id: marker.id, coords: norm, color, stops: [], label: null });
							break;
						} catch (je) {`;

const replace1 = `norm = safeNormalizeCoords(data && data.coords);
						} catch (je) {`;

content = content.replace(target1, replace1);

const target2 = `norm = safeNormalizeCoords(data && data.coords);
							try { console.debug('[map] leg-geometry selected route_tracks (post-route)', { line: String(line), rid, coordsLen: norm ? norm.length : 0 }); } catch (e) { /* ignore */ }
							const color = busIconColor(marker.delayMinutes);
							setSelectedVehicleTrack((prev) => (prev && prev.id === marker.id) ? { ...prev, coords: norm, color } : { id: marker.id, coords: norm, color, stops: [], label: null });
							break;
						} catch (je) {`;

const replace2 = `norm = safeNormalizeCoords(data && data.coords);
						} catch (je) {`;

content = content.replace(target2, replace2);

// Fix USER_ICON ReferenceError by replacing USER_ICON with createUserIcon() on line 1974
content = content.replace('icon={USER_ICON}', 'icon={createUserIcon()}');

fs.writeFileSync(file, content, 'utf8');
