/**
 * Live Updates Service using WebSocket/STOMP
 * Handles real-time updates for train movements and other live data
 */

import { Client } from '@stomp/stompjs';

class LiveUpdatesManager {
  constructor() {
    this.client = null;
    this.subscriptions = new Map();
    this.messageHandlers = new Map();
    this.isConnected = false;
  }

  /**
   * Initialize and connect to the live updates broker
   * @param {Object} options - Connection options
   * @returns {Promise<void>}
   */
  connect(options = {}) {
    return new Promise((resolve, reject) => {
      const brokerURL = options.brokerURL || 'wss://transport.scc.lancs.ac.uk:61613';
      
      this.client = new Client({
        brokerURL,
        connectHeaders: {
          login: options.login || 'guest',
          passcode: options.passcode || 'guest',
          host: '/'
        },
        onConnect: () => {
          this.isConnected = true;
          console.log('Connected to live updates broker');
          resolve();
        },
        onStompError: (error) => {
          console.error('STOMP error:', error);
          reject(error);
        },
        onDisconnect: () => {
          this.isConnected = false;
          console.log('Disconnected from live updates broker');
        },
        reconnectDelay: 5000,
        heartbeatIncoming: 4000,
        heartbeatOutgoing: 4000,
      });

      this.client.activate();
    });
  }

  /**
   * Subscribe to a specific topic
   * @param {string} topic - Topic path (e.g., '/topic/TRAIN_MVT_ALL_TOC')
   * @param {Function} callback - Callback function for messages
   * @returns {string} Subscription ID for unsubscribing
   */
  subscribe(topic, callback) {
    if (!this.isConnected) {
      console.warn('Not connected to broker. Please connect first.');
      return null;
    }

    const subscriptionId = `sub-${Date.now()}-${Math.random()}`;
    
    try {
      const subscription = this.client.subscribe(topic, (message) => {
        try {
          const data = JSON.parse(message.body);
          callback(data);
        } catch (error) {
          console.error('Error parsing message:', error);
          callback(message.body);
        }
      });

      this.subscriptions.set(subscriptionId, subscription);
      this.messageHandlers.set(subscriptionId, callback);
      console.log(`Subscribed to ${topic} with ID: ${subscriptionId}`);

      return subscriptionId;
    } catch (error) {
      console.error('Error subscribing to topic:', error);
      return null;
    }
  }

  /**
   * Unsubscribe from a topic
   * @param {string} subscriptionId - The subscription ID returned from subscribe()
   */
  unsubscribe(subscriptionId) {
    const subscription = this.subscriptions.get(subscriptionId);
    if (subscription) {
      subscription.unsubscribe();
      this.subscriptions.delete(subscriptionId);
      this.messageHandlers.delete(subscriptionId);
      console.log(`Unsubscribed from ${subscriptionId}`);
    }
  }

  /**
   * Subscribe to train movement updates for all TOCs
   * @param {Function} callback - Callback for train movement data
   * @returns {string} Subscription ID
   */
  subscribeToBusMovements(callback) {
    // Bus movement STOMP/topics are disabled in this deployment —
    // the frontend fetches bus locations via HTTP polling instead.
    console.warn('subscribeToBusMovements: bus STOMP subscription disabled; use HTTP polling for buses');
    return null;
  }

  /**
   * Subscribe to train movement updates for all TOCs
   * @param {Function} callback - Callback for train movement data
   * @returns {string} Subscription ID
   */
  subscribeToTrainMovements(callback) {
    return this.subscribe('/topic/TRAIN_MVT_ALL_TOC', callback);
  }

  /**
   * Subscribe to train delay updates
   * @param {Function} callback - Callback for delay data
   * @returns {string} Subscription ID
   */
  subscribeToTrainDelays(callback) {
    return this.subscribe('/topic/TD_ALL_SIG_AREA', callback);
  }

  /**
   * Subscribe to service alert updates
   * @param {Function} callback - Callback for alert data
   * @returns {string} Subscription ID
   */
  subscribeToAlerts(callback) {
    return this.subscribe('/topic/SERVICE_ALERTS', callback);
  }

  /**
   * Publish a message to a topic
   * @param {string} destination - Topic destination
   * @param {Object} body - Message body
   * @param {Object} headers - Optional headers
   */
  publish(destination, body, headers = {}) {
    if (!this.isConnected) {
      console.warn('Not connected to broker.');
      return;
    }

    try {
      this.client.publish({
        destination,
        body: JSON.stringify(body),
        headers
      });
      console.log(`Published to ${destination}`);
    } catch (error) {
      console.error('Error publishing message:', error);
    }
  }

  /**
   * Disconnect from the broker
   * @returns {Promise<void>}
   */
  disconnect() {
    return new Promise((resolve) => {
      if (this.client) {
        this.client.deactivate(() => {
          this.isConnected = false;
          this.subscriptions.clear();
          this.messageHandlers.clear();
          console.log('Disconnected from live updates');
          resolve();
        });
      } else {
        resolve();
      }
    });
  }

  /**
   * Get connection status
   * @returns {boolean}
   */
  getConnectionStatus() {
    return this.isConnected;
  }

  /**
   * Unsubscribe from all topics
   */
  unsubscribeAll() {
    for (const [subscriptionId] of this.subscriptions) {
      this.unsubscribe(subscriptionId);
    }
  }
}

// Export singleton instance
export const liveUpdatesManager = new LiveUpdatesManager();

export default LiveUpdatesManager;
