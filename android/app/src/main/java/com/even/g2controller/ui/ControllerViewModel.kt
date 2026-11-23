package com.even.g2controller.ui

import androidx.lifecycle.LiveData
import androidx.lifecycle.MutableLiveData
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.even.g2controller.ble.BleManager
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.channels.consumeEach

class ControllerViewModel(
    private val bleManager: BleManager
) : ViewModel() {
    
    sealed interface UiState {
        data class Disconnected(val message: String? = null) : UiState
        data class Connecting(val deviceName: String) : UiState
        object Connected : UiState
        data class Error(val message: String) : UiState
    }
    
    private val _uiState = MutableLiveData<UiState>(UiState.Disconnected())
    val uiState: LiveData<UiState> = _uiState
    
    private val _selectedDevice = MutableLiveData<String?>(null)
    val selectedDevice: LiveData<String?> = _selectedDevice
    
    init {
        observeBleState()
    }
    
    private fun observeBleState() {
        viewModelScope.launch {
            bleManager.stateFlow.consumeEach { state ->
                _uiState.value = when (state) {
                    is BleManager.ConnectionState.Disconnected -> UiState.Disconnected(state.reason)
                    is BleManager.ConnectionState.Connecting -> UiState.Connecting(state.deviceName)
                    BleManager.ConnectionState.Connected -> UiState.Connected
                    is BleManager.ConnectionState.Error -> UiState.Error(state.message)
                }
            }
        }
    }
    
    fun startScan() {
        // TODO: implement scan
    }
    
    fun connect(deviceName: String) {
        _selectedDevice.value = deviceName
    }
    
    fun disconnect() {
        bleManager.disconnect()
    }
    
    fun scrollUp() = sendCommand(com.even.g2controller.ble.G2Commands.scrollUp())
    fun scrollDown() = sendCommand(com.even.g2controller.ble.G2Commands.scrollDown())
    fun tap() = sendCommand(com.even.g2controller.ble.G2Commands.tap())
    fun longTap() = sendCommand(com.even.g2controller.ble.G2Commands.longTap())
    fun doubleTap() = sendCommand(com.even.g2controller.ble.G2Commands.doubleTap())
    fun navBack() = sendCommand(com.even.g2controller.ble.G2Commands.navBack())
    fun navHome() = sendCommand(com.even.g2controller.ble.G2Commands.navHome())
    
    private fun sendCommand(data: ByteArray) {
        viewModelScope.launch {
            bleManager.sendCommand(data)
        }
    }
}
