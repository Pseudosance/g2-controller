package com.even.g2controller.ble

import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.bluetooth.le.BluetoothLeScanner
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.content.Context
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.LifecycleOwner
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.ReceiveChannel
import kotlinx.coroutines.launch

class BleManager(
    private val context: Context,
    private val scope: CoroutineScope
) : LifecycleEventObserver {
    private val adapter: BluetoothAdapter by lazy {
        val bm = context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
        bm.adapter
    }
    private val scanner: BluetoothLeScanner by lazy { adapter.bluetoothLeScanner }
    
    private var currentClient: EusClient? = null
    private var targetDevice: BluetoothDevice? = null
    private val stateChannel = Channel<ConnectionState>(Channel.UNLIMITED)
    val stateFlow: ReceiveChannel<ConnectionState> = stateChannel
    
    sealed interface ConnectionState {
        data class Disconnected(val reason: String? = null) : ConnectionState
        data class Connecting(val deviceName: String) : ConnectionState
        object Connected : ConnectionState
        data class Error(val message: String) : ConnectionState
    }
    
    fun startScan(onDeviceFound: (BluetoothDevice) -> Unit) {
        if (!adapter.isEnabled) return
        val callback = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult) {
                val device = result.device
                if (device.name?.contains("G2", true) == true || 
                    device.name?.contains("Even", true) == true) {
                    onDeviceFound(device)
                }
            }
        }
        scanner.startScan(callback)
        scope.launch(Dispatchers.IO) {
            kotlinx.coroutines.delay(30000)
            scanner.stopScan(callback)
        }
    }
    
    fun connect(device: BluetoothDevice) {
        targetDevice = device
        stateChannel.trySend(ConnectionState.Connecting(device.name ?: "G2"))
        scope.launch(Dispatchers.IO) {
            val client = EusClient(context, device,
                onStateChange = { connected ->
                    if (connected) {
                        stateChannel.trySend(ConnectionState.Connected)
                    } else {
                        stateChannel.trySend(ConnectionState.Disconnected("Connection lost"))
                        scheduleReconnect()
                    }
                },
                onNotification = { data ->
                }
            )
            currentClient = client
            if (!client.connect()) {
                stateChannel.trySend(ConnectionState.Error("Failed to initiate connection"))
            }
        }
    }
    
    private fun scheduleReconnect() {
        scope.launch(Dispatchers.IO) {
            kotlinx.coroutines.delay(5000)
            targetDevice?.let { connect(it) }
        }
    }
    
    suspend fun sendCommand(data: ByteArray): Boolean {
        return currentClient?.write(data) ?: false
    }
    
    fun disconnect() {
        currentClient?.disconnect()
        currentClient = null
        targetDevice = null
        stateChannel.trySend(ConnectionState.Disconnected("User disconnected"))
    }
    
    override fun onStateChanged(source: LifecycleOwner, event: Lifecycle.Event) {
        if (event == Lifecycle.Event.ON_DESTROY) {
            disconnect()
            stateChannel.close()
        }
    }
}
