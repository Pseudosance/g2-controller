package com.even.g2controller.ble

import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothGattService
import android.bluetooth.BluetoothProfile
import android.content.Context
import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import java.util.UUID

class EusClient(
    private val context: Context,
    private val device: BluetoothDevice,
    private val onStateChange: (Boolean) -> Unit,
    private val onNotification: (ByteArray) -> Unit
) {
    private var gatt: BluetoothGatt? = null
    private var txChar: BluetoothGattCharacteristic? = null
    private var rxChar: BluetoothGattCharacteristic? = null
    private val pendingWrites = Channel<ByteArray>(Channel.UNLIMITED)
    private val writeQueue = Channel<CompletableDeferred<Boolean>>(Channel.UNLIMITED)
    private var connected = false
    private val ioScope = CoroutineScope(Dispatchers.IO)
    
    private val gattCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                Log.w("EusClient", "Connection state change error: $status")
                cleanup()
                onStateChange(false)
                return
            }
            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    Log.d("EusClient", "Connected, discovering services...")
                    gatt.discoverServices()
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    Log.d("EusClient", "Disconnected")
                    cleanup()
                    onStateChange(false)
                }
            }
        }
        
        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                Log.w("EusClient", "Service discovery failed: $status")
                cleanup()
                onStateChange(false)
                return
            }
            val service = gatt.getService(UUID.fromString(EusUuids.SERVICE))
            if (service == null) {
                Log.e("EusClient", "EUS service not found")
                cleanup()
                onStateChange(false)
                return
            }
            txChar = service.getCharacteristic(UUID.fromString(EusUuids.TX_CHAR))
            rxChar = service.getCharacteristic(UUID.fromString(EusUuids.RX_CHAR))
            if (txChar == null || rxChar == null) {
                Log.e("EusClient", "TX/RX characteristics not found")
                cleanup()
                onStateChange(false)
                return
            }
            gatt.setCharacteristicNotification(rxChar!!, true)
            val cccd = rxChar!!.getDescriptor(UUID.fromString("00002902-0000-1000-8000-00805f9b34fb"))
            cccd?.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
            gatt.writeDescriptor(cccd!!)
            connected = true
            Log.d("EusClient", "EUS ready")
            onStateChange(true)
        }
        
        override fun onCharacteristicChanged(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic) {
            if (characteristic.uuid.toString().equals(EusUuids.RX_CHAR, true)) {
                characteristic.value?.let { onNotification(it) }
            }
        }
        
        override fun onCharacteristicWrite(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic, status: Int) {
            ioScope.launch {
                val result = writeQueue.receive()
                if (status == BluetoothGatt.GATT_SUCCESS) {
                    result.complete(true)
                } else {
                    Log.w("EusClient", "Write failed: $status")
                    result.complete(false)
                }
            }
        }
        
        override fun onDescriptorWrite(gatt: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
            if (status == BluetoothGatt.GATT_SUCCESS) {
                Log.d("EusClient", "Notifications enabled")
            }
        }
    }
    
    fun connect(): Boolean {
        gatt = device.connectGatt(context, false, gattCallback)
        return gatt != null
    }
    
    fun disconnect() {
        gatt?.disconnect()
        gatt?.close()
        cleanup()
    }
    
    private fun cleanup() {
        connected = false
        txChar = null
        rxChar = null
        gatt = null
    }
    
    suspend fun write(data: ByteArray): Boolean {
        if (!connected || txChar == null || gatt == null) return false
        val deferred = CompletableDeferred<Boolean>()
        writeQueue.send(deferred)
        pendingWrites.send(data)
        return deferred.await()
    }
}
